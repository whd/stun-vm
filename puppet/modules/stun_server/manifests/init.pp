class stun_server {
  $rpm_version = '0.96-6svc.amzn1'
  realize(File['mozilla-services-aws'])

  package {
    'stun-server':
      ensure   => $rpm_version,
  }

  circus::watcher {
    'stun-server':
      cmd          =>
      "/usr/sbin/stun-server \
-h $(circus.env.localipv4) -e $(circus.env.publicipv4)",
      hooks        => {
        # hook titles need to be unique across circus-managed modules
        "${module_name}_before_start" => {
          type        => 'before_start',
          module_file => "${module_name}.py",
          entry_point => 'set_ipv4'
        },
      },
      require      => [
        Package['stun-server'],
      ],
  }
}
