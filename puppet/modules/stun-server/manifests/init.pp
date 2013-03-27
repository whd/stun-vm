class stun-server {
  include daemontools
  $rpm_version = '0.96-6svc'
  realize(Yumrepo['mozilla'])

  package {
    'stun-server':
      ensure   => $rpm_version,
      require  => YumRepo['mozilla']
  }

  daemontools::setup {
    'stun-server':
      require => Package['stun-server'];
  }
}
