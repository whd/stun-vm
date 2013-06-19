class base::yum($moz_repocontent = undef){
  @file { 'mozilla-services-aws':
    ensure  => file,
    path    => '/etc/yum.repos.d/mozilla-services-aws.repo',
    content => $moz_repocontent,
  }
}
