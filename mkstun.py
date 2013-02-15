#!/usr/bin/env python
# routines for automated setup and deploy of stun servers in AWS regions
# TODO termination protection, detailed monitoring
from __future__ import print_function

import sys
import os
import argparse
import re
import time
from datetime import date

import boto
import boto.ec2
import boto.ec2.cloudwatch
import boto.sns

from argparse import RawDescriptionHelpFormatter

class ConfParser:
    """Routines for parsing argument vector."""
    @staticmethod
    def getparser ():
        parser = argparse.ArgumentParser(
            description="""stun server automated provisioning (via boto)
Assumes AWS credentials are in ~/.boto or are otherwise present""",
            epilog="""Use the "all" action (followed by a base instance id) to completely
provision an instance in a new region. You must specify REGION for every
invocation. Subcommand flags can be specified anywhere on the command line,
since they do not intersect. The various subcommands/stages are work as
follows:

[make-security-group]
Make the security group for the stun server (if non-existent).

[make-base-instance BASE_AMI_ID]
Creates a running instance of a stun-server using cloudinit from BASE_AMI_ID.

[test-instance INSTANCE]
Test a running stun-server instance using a simple stun-client check.
If the instance is not ready, spin until it is. Five attempts are made to
verify whether the stun server is running correctly.
The stun-client binary is assumed to exist and be in $PATH.

[make-ami TEST_INSTANCE]
Stop TEST_INSTANCE (if running) and generate an AMI from the stopped instance.
Assumes the instance was already tested for fitness previously.

[make-instance AMI [--size SIZE] [--env ENV] [--ip IP]]

Make an actual running instance of AMI. Assign tags as appropriate. With IP,
set the elastic IP of this instance to IP (already allocated), otherwise
allocate a new EIP and associate it with the instance.

[create-sns-topic]
Create an SNS topic for cloudwatch alerts.

[create-subscription ARN]
Subscribe an email to topic ARN (--email required).

[create-alarm ARN INSTANCE]
Create a health-check alarm for INSTANCE to publish to ARM.

[all BASE_AMI_ID]
All of the above, chained so that only BASE_AMI_ID need be specified.""",
            formatter_class=RawDescriptionHelpFormatter)
        parser.add_argument(
            '--enable-ssh', action='store_true',
            help="enable ssh access to the hosts")
        parser.add_argument(
            '--email', help="Email for SNS topic integration")
        parser.add_argument(
            '--env', help="Env tag (default prod)")
        parser.add_argument(
            '--size', help="instance size (default m1.small)")
        parser.add_argument(
            '--ip', help="use elastic ip IP (default allocates a new one)")
        parser.add_argument(
            '--az', help="specify availability zone (default unspecified)")

        parser.add_argument('region', metavar='REGION')
        parser.add_argument('action', metavar='ACTION', nargs='+')
        return parser

def get_tags (conf):
    default = {
        'Env': 'prod',
        'Name': 'stun-server',
        'App': 'webrtc',
        'Type': 'stun'
    }
    if conf.env is not None:
        default['Env'] = conf.env
    return default

def make_security_group(conn, conf):
    """Create a new security group and return it."""
    # copy_to_region() exists, but it's pretty easy to make manually.
    name = 'webrtc-stun-server'
    desc = 'stun servers for webrtc'
    # check for existing group, and return it if it exists
    rs = [x for x in conn.get_all_security_groups() if x.name == name]
    if len(rs) > 0:
        print("%s already has webrtc stun security group" % conf.region)
        return rs[0]
    # make the new security group
    web = conn.create_security_group(name, desc)
    # ssh
    web.authorize('tcp', 22, 22, '63.245.208.0/20')
    # stun traffic
    web.authorize('tcp', 3478, 3478, '0.0.0.0/0')
    web.authorize('udp', 3478, 3478, '0.0.0.0/0')
    # icmp echo requests
    web.authorize('icmp', 8, -1, '0.0.0.0/0')
    return web

def script():
    """Return the cloud-init script used to provision a stun AMI."""
    return """#!/bin/sh
# load up stun configuration
yum install -y puppet git
cd /tmp
git clone https://github.com/whd/stun-vm
puppet apply --modulepath=stun-vm/puppet/modules stun-vm/puppet/bootstrap.pp
rm -rf stun-vm
rm /var/tmp/*.rpm
"""

def keyname (conf):
    """Return the svcops keyname for this region."""
    # FIXME make sure this key exists
    return 'svcops-sl62-base-key-%s' % conf.region

def make_base_instance(conn, conf):
    """Make a base stun server instance using cloudinit."""
    if not conn.get_image(conf.base_ami_id):
        print("unable to find %s base AMI %s in %s" %
              (conf.base_ami_id, conf.region))
        exit(1)

    return get_instance(conn.run_instances(
        image_id=conf.base_ami_id,
        instance_type=conf.test_instance_size,
        security_groups=['webrtc-stun-server'],
        user_data=script()
    ))

def stun_check (ip_or_dns):
    """What will become the Dynect health check for the service."""
    # FIXME make sure stun-client exists (RPM is stun)
    print("running stun check (may hang) use ^C to interrupt")
    return os.system("stun-client -v %s 1 2>&1 | grep mozilla" % ip_or_dns) == 0

def test_instance(conn, conf, reservation):
    """Test a running stun server."""
    # currently there is only ever one instance invoked
    instance = get_instance(reservation)

    if instance.state != 'pending' and instance.state != 'running':
        print("error: instance %s not pending or running (%s)" +
              ", try starting it" % (instance, instance.state))
        exit(1)
    while instance.state != 'running':
        print("test_instance is %s, sleeping 60s..." % instance.state)
        time.sleep(60)
        instance.update()

    i = 0
    while i <= conf.tries:
        if stun_check(instance.public_dns_name):
            print("stun check complete, instance is operational")
            print(instance.id)
            return True
        print("test_instance failed, sleeping 60s and trying again...")
        i += 1
        instance.update()
        time.sleep(60)
    return False

def make_name():
    """Generate a proper AMI name."""
    today = date.today().strftime('%Y.%m.%d.1')
    return "mozsvc-ami-pv-webrtcstun-%s.x86_64-ebs" % today

def get_instance (res_or_instance):
    """Return the single instance associated with a RES_OR_INSTANCE."""
    try:
        instance = res_or_instance.instances[0]
    except:
        instance = res_or_instance
    return instance

def make_ami(conn, conf, reservation):
    """Generate a golden AMI from the provided instance."""
    instance = get_instance(reservation)
    return conn.create_image(instance.id,
                             make_name(), description="stun server")

def make_instance(conn, conf, ami_id):
    """Make an actual stun-server instance using AMI_ID."""
    reservation = conn.run_instances(
        image_id=ami_id,
        placement=conf.az,
        instance_type=conf.prod_instance_size,
        security_groups=['webrtc-stun-server'],
    )
    instance = reservation.instances[0]
    print("tagging instance %s" % instance)
    tag(instance, get_tags(conf))
    while instance.state != 'running':
        print("instance not running (%s), sleeping 60s..." % instance.state)
        time.sleep(60)
        instance.update()

    print("associating an elastic ip with instance %s" % instance)
    if conf.ip is None:
        print("allocating a new elastic ip")
        addr = conn.allocate_address().public_ip
        print("new address is %s" % addr)
    else:
        addr = conf.elastic_ip

    if not conn.associate_address(instance.id, addr):
        print("unable to associate elastic ip %s with %s" % (addr, instance))
        exit(1)

    instance.update()

    if not test_instance(conn, conf, instance):
        print("unable to verify working instance %s" % instance)
        exit(1)

    return instance

def tag (instance, tags):
    """Tag INSTANCE with TAGS, a dict."""
    for key, val in tags.iteritems():
        instance.add_tag(key, val)

def get_reservation (conn, instance_id):
    reservations = conn.get_all_instances(filters={'instance-id': instance_id})
    if len(reservations) == 0:
        print("error: couldn't find instance with id %s" % instance_id)
    return reservations[0]

def get_region_connection(conf):
    regions = boto.ec2.regions()
    if conf.region not in [x.name for x in regions]:
        print('error: no such region %s' % conf.region)
        exit(1)
    region = [x for x in regions if x.name == conf.region][0]
    return region

def check_availability_zone (conn, conf):
    zones = conn.get_all_zones()
    if conf.az is not None and conf.az not in [x.name for x in zones]:
        print('error: no such region %s' % conf.region)
        exit(1)

def create_sns_topic (conn2, conf):
    """Create a webrtc-ops topic in this region if one does not already exist.
conn2 is a connection to sns, NOT ec2."""
    topics = conn2.get_all_topics()['ListTopicsResponse']['ListTopicsResult']['Topics']
    topic = [x for x in topics if re.search('webrtc', x['TopicArn'])]

    if any(topic):
        print('sns topic %s already exists' % topic[0]['TopicArn'])
        return topic[0]['TopicArn']
    else:
        print("create new topic for webrtc-ops")
        response = conn2.create_topic('webrtc-ops')
        arn = response['CreateTopicResponse']['CreateTopicResult']['TopicArn']
        print("created topic %s" % arn)
        return arn

def create_subscription (conn2, conf, arn):
    """... and associated subscription."""
    subscriptions = conn2.get_all_subscriptions_by_topic(arn)['ListSubscriptionsByTopicResponse']['ListSubscriptionsByTopicResult']['Subscriptions']
    subscription = [x for x in subscriptions if x['Endpoint'] == conf.email]
    if any(subscription):
        print("already have subscription for topic %s" % arn)
        return subscription[0]
    else:
        if conf.email is None:
            print("error, no email provided")
            exit(1)
        print('subscribing to topic %s' % arn)
        return conn2.subscribe(arn, 'email', conf.email)

def create_alarm (conn3, conf, arn, instance_id):
    """... and associated alarm."""
    mname = 'StatusCheckFailed'
    metric = conn3.list_metrics(
        dimensions={'InstanceId': instance_id},
        # metric_name=mname
    )

    if any(metric):
        metric = metric[0]
    else:
        print("error: metric %s not found for instance %s (wait a while?)" %
              (mname, instance_id))
        print("command to run is: \n%s %s create-alarm %s %s\n"
              % (__file__, conf.region, arn, instance_id))
        exit(1)
    print(metric)
    alarm = metric.create_alarm(name=mname, comparison='>=',
                                threshold=1, period=300,
                                evaluation_periods=2,
                                statistic='Maximum',
                                alarm_actions=[arn],
                                # ok_actions=[topic_arn]
    )
    return alarm

# main logic
if __name__ == '__main__':
    conf = ConfParser.getparser().parse_args ()
    conf.tries = 5
    conf.test_instance_size = 't1.micro'
    conf.prod_instance_size = 'm1.small'
    # fixme verify size is an acceptable value
    if conf.size is not None:
        conf.prod_instance_size = conf.size

    region = get_region_connection(conf)
    conn = region.connect ()
    sns_conn = boto.sns.connect_to_region(conf.region)
    cw_conn = boto.ec2.cloudwatch.connect_to_region(conf.region)

    check_availability_zone(conn, conf)

    # fixme make this suck less
    if conf.action[0] == 'make-security-group':
        print(make_security_group(conn, conf))
    elif conf.action[0] == 'make-base-instance':
        conf.base_ami_id = conf.action[1]
        print(make_base_instance(conn, conf))
    elif conf.action[0] == 'test-instance':
        reservation = get_reservation(conn, conf.action[1])
        instance = reservation.instances[0]
        if not test_instance(conn, conf, instance):
            print("unable to verify working instance %s" % instance)
            exit(1)
    elif conf.action[0] == 'make-ami':
        reservation = get_reservation(conn, conf.action[1])
        ami_id = make_ami(conn, conf, reservation)
        print("AMI: %s" % ami_id)
    elif conf.action[0] == 'make-instance':
        make_instance(conn, conf, conf.action[1])
    elif conf.action[0] == 'create-sns-topic':
        print(create_sns_topic(sns_conn, conf))
    elif conf.action[0] == 'create-subscription':
        print(create_subscription(sns_conn, conf, conf.action[1]))
    elif conf.action[0] == 'create-alarm':
        print(create_alarm(cw_conn, conf, conf.action[1], conf.action[2]))

    if conf.action[0] != 'all':
        exit()
    if conf.email is None:
        print("--email required for ALL")
        exit(1)

    conf.base_ami_id = conf.action[1]

    if conf.base_ami_id is None:
        print("base ami id required")

    instance = make_base_instance(conn, conf)
    if not test_instance(conn, conf, instance):
        print("unable to verify working instance %s" % instance)
        exit(1)

    ami_id = make_ami(conn, conf, instance)
    print("AMI: %s" % ami_id)

    ami = conn.get_image(ami_id)
    while ami.state != 'available':
        print("spinning until ami %s is available" % ami)
        time.sleep(30)
        ami.update()

    print("stop test instance")
    conn.stop_instances(instance_ids=[instance.id])

    print("spinning up an instance")
    instance2 = make_instance(conn, conf, ami_id)

    print("terminate test instance")
    conn.terminate_instances(instance_ids=[instance.id])

    print("create topic")
    arn = create_sns_topic(sns_conn, conf)
    print("subscribe to topic")
    create_subscription(sns_conn, conf, arn)
    print("adding alarm to topic")
    create_alarm(cw_conn, conf, arn, instance2.id)

    print("instance %s running with public DNS %s" %
          (instance2, instance2.public_dns_name))

    print("All OK")
