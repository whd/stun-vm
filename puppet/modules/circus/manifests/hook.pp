# FIXME document this interface
define circus::hook($module_file, $type, $entry_point, $caller) {
  if ! defined(File["${circus::manager::hooks_dir}/${module_file}"]) {
    file {"${circus::manager::hooks_dir}/${module_file}":
      ensure  => file,
      source  => "puppet:///modules/${caller}/circushooks/${module_file}",
      require => File[$circus::manager::hooks_dir],
    }
  }
}
