class daemontools::base {
  $daemontools_version = '0.76-1moz'
  realize Yumrepo['mozilla']

  package {
    'daemontools':
      ensure   => $daemontools_version,
      require  => Yumrepo['mozilla'];
  }

  # RHEL6 upstart isn't backwards compatible with inittab,
  # so we have to put in an entry to start daemontools
  file {
    '/etc/init/daemontools.conf':
      ensure  => file,
      notify  => Exec['start-daemontools'],
      source  => 'puppet:///modules/daemontools/daemontools.conf',
      require => Package['daemontools'];
  }

  exec {
    'start-daemontools':
      command => '/sbin/initctl start daemontools',
      unless  => '/sbin/initctl status daemontools | grep -qw start/running',
      require => Package['daemontools'];
  }

  file {
    '/var/log/services':
      ensure  => directory,
      owner   => 'root',
      group   => 'root',
      mode    => '0755';
    '/var/services/README':
      ensure  => file,
      owner   => 'root',
      group   => 'root',
      mode    => '0444',
      source  => 'puppet:///modules/daemontools/README',
      require => File['/var/services'];
    '/var/services':
      ensure  => directory,
      owner   => 'root',
      group   => 'root',
      mode    => '0755',
      require => Package['daemontools'];
  }
}
