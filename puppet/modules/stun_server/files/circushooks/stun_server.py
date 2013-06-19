import urllib2


def set_ipv4(watcher, arbiter, hook_name):
    local = urllib2.urlopen(
        'http://169.254.169.254/latest/meta-data/local-ipv4/').read()
    public = urllib2.urlopen(
        'http://169.254.169.254/latest/meta-data/public-ipv4/').read()
    watcher.env['localipv4'] = local
    watcher.env['publicipv4'] = public
    print watcher.env['localipv4']
    print watcher.env['publicipv4']
    return True
