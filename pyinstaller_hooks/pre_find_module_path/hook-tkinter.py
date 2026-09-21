import sysconfig


def pre_find_module_path(hook_api):
    stdlib = sysconfig.get_path("stdlib")
    if stdlib:
        hook_api.search_dirs = [stdlib]
