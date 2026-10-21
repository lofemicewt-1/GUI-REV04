"""Stream B translations for the round-two preset toolbar."""

from config.languages import LANGUAGES


ZH = {
    "r2b_toolbar_software_title": "软件",
    "r2b_toolbar_board_title": "上电启动",
    "r2b_toolbar_software_help": "预设示例和导入会写入当前芯片；导出只保存到电脑文件。",
    "r2b_toolbar_board_help": "这里是开发板脱机槽位。设为板上按键用的槽位不会在上电时自动加载；其他操作请看各自说明。",
    "r2b_toolbar_aux_menu_help": "读取操作。选择后会按原操作读取，不会合并为载入或开始检测。",
    "r2b_aux_read_current_settings": "读取当前芯片设置",
    "r2b_aux_read_slot_into_page": "读出槽位到页面",
}

EN = {
    "r2b_toolbar_software_title": "Software",
    "r2b_toolbar_board_title": "Power-on",
    "r2b_toolbar_software_help": "Preset examples and imports write to the current chip; export only saves a computer file.",
    "r2b_toolbar_board_help": "Board offline slots are managed here. Setting a slot for the board button does not load it automatically at power-on; see each action's help for its behavior.",
    "r2b_toolbar_aux_menu_help": "Read actions. Each keeps its existing read behavior and does not imply loading a slot or starting detection.",
    "r2b_aux_read_current_settings": "Read current chip settings",
    "r2b_aux_read_slot_into_page": "Read slot into page",
}


def install() -> None:
    LANGUAGES["zh"].update(ZH)
    LANGUAGES["en"].update(EN)
