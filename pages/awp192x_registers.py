from __future__ import annotations

import re

from core.register_base import BitFieldDefinition, RegisterAccess, RegisterDefinition


def _field(
    high_bit: int,
    low_bit: int,
    key: str,
    label: str,
    tooltip: str = "",
    *,
    read_label: str | None = None,
    read_tooltip: str | None = None,
    write_label: str | None = None,
    write_tooltip: str | None = None,
    choices: dict[int, str] | None = None,
) -> BitFieldDefinition:
    return BitFieldDefinition(
        high_bit,
        low_bit,
        key,
        label,
        tooltip,
        read_label=read_label,
        read_tooltip=read_tooltip,
        write_label=write_label,
        write_tooltip=write_tooltip,
        choices=choices or {},
    )


BOOL_CHOICES = {
    0: "Disable",
    1: "Enable",
}

FIFO_DEPTH_CHOICES = {
    0x0: "1024 points",
    0x1: "960 points",
    0x2: "896 points",
    0x3: "832 points",
    0x4: "768 points",
    0x5: "704 points",
    0x6: "640 points",
    0x7: "576 points",
    0x8: "512 points",
    0x9: "448 points",
    0xA: "384 points",
    0xB: "320 points",
    0xC: "256 points",
    0xD: "192 points",
    0xE: "128 points",
    0xF: "64 points",
}

ADC_AVERAGE_CHOICES = {
    0x0: "input",
    0x1: "1/2 input + 1/2 average",
    0x2: "1/4 input + 3/4 average",
    0x3: "1/8 input + 7/8 average",
    0x4: "1/16 input + 15/16 average",
    0x5: "1/32 input + 31/32 average",
    0x6: "1/32 input + 31/32 average",
    0x7: "1/32 input + 31/32 average",
    0x8: "1/32 input + 31/32 average",
    0x9: "1/32 input + 31/32 average",
    0xA: "1/32 input + 31/32 average",
    0xB: "1/32 input + 31/32 average",
    0xC: "1/32 input + 31/32 average",
    0xD: "1/32 input + 31/32 average",
    0xE: "1/32 input + 31/32 average",
    0xF: "1/32 input + 31/32 average",
}

ADC_OVV_TIME_CHOICES = {
    0x0: "1 time",
    0x1: "2 times",
    0x2: "4 times",
    0x3: "8 times",
    0x4: "16 times",
    0x5: "32 times",
    0x6: "64 times",
    0x7: "128 times",
    0x8: "256 times",
    0x9: "512 times",
    0xA: "1023 times",
    0xB: "1023 times",
    0xC: "1023 times",
    0xD: "1023 times",
    0xE: "1023 times",
    0xF: "1023 times",
}

DATA_DEPTH_CHOICES = {
    0x0: "1024",
    0x1: "512",
    0x2: "256",
    0x3: "128",
    0x4: "64",
    0x5: "32",
    0x6: "16",
    0x7: "8",
}

DAC_MODE_CHOICES = {
    0x0: "Direct mode",
    0x1: "FIFO mode",
    0x2: "SRAM or ROM playback mode",
    0x3: "SRAM read/write mode",
}

PLAY_STOP_CHOICES = {
    0: "No command",
    1: "Stop playback",
}

PLAY_GO_CHOICES = {
    0: "No command",
    1: "Start playback",
}

ADC_STOP_CHOICES = {
    0: "No command",
    1: "Stop ADC",
}

ADC_GO_CHOICES = {
    0: "No command",
    1: "Start ADC",
}

WAVE_NUMBER_CHOICES = {
    **{index: f"SRAM slice {index}" for index in range(0x1E)},
    0x1E: "Serial order",
    0x1F: "ROM sine",
}

SERIAL_WAVE_CHOICES = {
    **{index: f"SRAM slice {index}" for index in range(0x1E)},
    0x1E: "Stop serial cycle",
    0x1F: "Stop serial cycle",
}

LOOP3_CHOICES = {value: f"{value + 1} loops" for value in range(8)}

COMMAND_CHOICES = {
    0x0000: "Default / no command",
    0x5283: "Reset",
    0x5350: "Sleep",
    0x5337: "Shutdown",
    0x5F76: "H-bridge clear for 25 us",
}


def _event_register(
    address: int,
    summary: str,
    enable_key: str,
    enable_label: str,
) -> RegisterDefinition:
    return RegisterDefinition(
        address=address,
        width=16,
        summary=summary,
        default_value=0x001F,
        fields=[
            _field(15, 15, enable_key, enable_label, choices=BOOL_CHOICES),
            _field(14, 14, "endless", "Endless", "Loop this playback event continuously.", choices=BOOL_CHOICES),
            _field(13, 5, "loop_time", "Loop time", "Playback count is loop time + 1."),
            _field(
                4,
                0,
                "wave_number",
                "Wave number",
                "0x00-0x1D select SRAM slices, 0x1E selects serial order, 0x1F selects ROM sine.",
                choices=WAVE_NUMBER_CHOICES,
            ),
        ],
    )


def _serial_register(address: int, summary: str, high_index: int, low_index: int) -> RegisterDefinition:
    return RegisterDefinition(
        address=address,
        width=16,
        summary=summary,
        default_value=0xF8F8,
        fields=[
            _field(
                15,
                11,
                f"serial_{high_index}",
                f"Serial {high_index}",
                "Wave number. 0x1E or 0x1F stops the current serial cycle.",
                choices=SERIAL_WAVE_CHOICES,
            ),
            _field(
                10,
                8,
                f"serial_{high_index}_loop_time",
                f"Serial {high_index} loop time",
                "Loop count is loop time + 1.",
                choices=LOOP3_CHOICES,
            ),
            _field(
                7,
                3,
                f"serial_{low_index}",
                f"Serial {low_index}",
                "Wave number. 0x1E or 0x1F stops the current serial cycle.",
                choices=SERIAL_WAVE_CHOICES,
            ),
            _field(
                2,
                0,
                f"serial_{low_index}_loop_time",
                f"Serial {low_index} loop time",
                "Loop count is loop time + 1.",
                choices=LOOP3_CHOICES,
            ),
        ],
    )


AWP192X_DETAIL_REGISTER_DEFINITIONS: tuple[RegisterDefinition, ...] = (
    RegisterDefinition(
        address=0x00,
        width=16,
        summary="SRAM data",
        default_value=0x0000,
        fields=[
            _field(15, 12, "reserved", "Reserved"),
            _field(11, 0, "data", "Data[11:0]", "SRAM or FIFO data payload."),
        ],
    ),
    RegisterDefinition(
        address=0x01,
        width=16,
        summary="System command",
        access=RegisterAccess.WRITE_ONLY,
        default_value=0x0000,
        note="Write-only command register.",
        fields=[
            _field(
                15,
                0,
                "command",
                "Command",
                "Write command word.",
                choices=COMMAND_CHOICES,
            )
        ],
    ),
    RegisterDefinition(
        address=0x02,
        width=16,
        summary="Status flags",
        access=RegisterAccess.READ_ONLY,
        default_value=0x0002,
        fields=[
            _field(15, 15, "bst_out_h", "bst_out_h"),
            _field(14, 14, "on_play", "On_Play"),
            _field(13, 13, "adc_over_h", "ADC_Over_H"),
            _field(12, 12, "adc_over_l", "ADC_Over_L"),
            _field(11, 11, "adc_over_h_posedge", "ADC_Over_H_Posedge"),
            _field(10, 10, "adc_over_h_negedge", "ADC_Over_H_Negedge"),
            _field(9, 9, "adc_over_l_posedge", "ADC_Over_L_Posedge"),
            _field(8, 8, "adc_over_l_negedge", "ADC_Over_L_Negedge"),
            _field(7, 7, "reserved_7", "Reserved"),
            _field(6, 6, "unv", "UNV"),
            _field(5, 5, "reserved_5", "Reserved"),
            _field(4, 4, "ovt", "OVT"),
            _field(3, 3, "half_empty", "Half_Empty"),
            _field(2, 2, "full", "Full"),
            _field(1, 1, "empty", "Empty"),
            _field(0, 0, "adc_busy", "ADC_Busy"),
        ],
    ),
    RegisterDefinition(
        address=0x03,
        width=16,
        summary="Status interrupt mask",
        default_value=0x0000,
        fields=[
            _field(15, 15, "m_bst_out_h", "M_bst_out_h"),
            _field(14, 14, "m_on_play", "M_On_Play"),
            _field(13, 13, "m_adc_over_h", "M_ADC_Over_H"),
            _field(12, 12, "m_adc_over_l", "M_ADC_Over_L"),
            _field(11, 11, "m_adc_over_h_posedge", "M_ADC_Over_H_Posedge"),
            _field(10, 10, "m_adc_over_h_negedge", "M_ADC_Over_H_Negedge"),
            _field(9, 9, "m_adc_over_l_posedge", "M_ADC_Over_L_Posedge"),
            _field(8, 8, "m_adc_over_l_negedge", "M_ADC_Over_L_Negedge"),
            _field(7, 7, "reserved_7", "Reserved"),
            _field(6, 6, "m_unv", "M_UNV"),
            _field(5, 5, "reserved_5", "Reserved"),
            _field(4, 4, "m_ovt", "M_OVT"),
            _field(3, 3, "m_half_empty", "M_Half_Empty"),
            _field(2, 2, "m_full", "M_Full"),
            _field(1, 1, "m_empty", "M_Empty"),
            _field(0, 0, "m_adc_busy", "M_ADC_Busy"),
        ],
    ),
    RegisterDefinition(
        address=0x04,
        width=16,
        summary="Global configuration",
        default_value=0x0300,
        fields=[
            _field(15, 12, "fifo_depth", "FiFO_DEPTH", choices=FIFO_DEPTH_CHOICES),
            _field(11, 11, "str_auto_addr", "STR", "Auto increment I2C register address.", choices=BOOL_CHOICES),
            _field(10, 10, "dis_plt", "DIS_PLT", "Disable auto threshold comparison.", choices=BOOL_CHOICES),
            _field(9, 9, "auto_end", "AUTO_END", choices=BOOL_CHOICES),
            _field(8, 8, "auto_zero", "AUTO_ZERO", choices=BOOL_CHOICES),
            _field(7, 6, "reserved_7_6", "Reserved"),
            _field(5, 4, "reg_lock", "REG_LOCK", "0x3 locks register writes until shutdown or hardware reset."),
            _field(3, 3, "clk_sync", "CLK_SYNC", "Clock sync enable.", choices=BOOL_CHOICES),
            _field(2, 2, "clk_out", "CLK_OUT", "Clock output enable.", choices=BOOL_CHOICES),
            _field(1, 1, "reserved_1", "Reserved"),
            _field(0, 0, "sync", "SYNC", "Synchronous playback enable.", choices=BOOL_CHOICES),
        ],
    ),
    RegisterDefinition(
        address=0x06,
        width=16,
        summary="Playback and ADC commands",
        access=RegisterAccess.WRITE_ONLY,
        default_value=0x0000,
        fields=[
            _field(15, 4, "reserved", "Reserved"),
            _field(3, 3, "play_stop", "Play_Stop", choices=PLAY_STOP_CHOICES),
            _field(2, 2, "play_go", "Play_Go", choices=PLAY_GO_CHOICES),
            _field(1, 1, "adc_stop", "ADC_Stop", choices=ADC_STOP_CHOICES),
            _field(0, 0, "adc_go", "ADC_Go", choices=ADC_GO_CHOICES),
        ],
    ),
    RegisterDefinition(
        address=0x07,
        width=16,
        summary="ADC readback",
        access=RegisterAccess.READ_ONLY,
        default_value=0x0800,
        fields=[
            _field(15, 15, "adc_pol", "ADC_POL"),
            _field(14, 14, "reserved_14", "Reserved"),
            _field(13, 13, "adc_gain", "ADC_Gain"),
            _field(12, 12, "reserved_12", "Reserved"),
            _field(11, 0, "adc_date", "ADC_Date", "12-bit ADC conversion data."),
        ],
    ),
    RegisterDefinition(
        address=0x08,
        width=16,
        summary="ADC high threshold",
        default_value=0x0FFF,
        fields=[
            _field(15, 12, "reserved", "Reserved"),
            _field(11, 0, "adc_threshold_h", "ADC_Threshold_H"),
        ],
    ),
    RegisterDefinition(
        address=0x09,
        width=16,
        summary="ADC low threshold",
        default_value=0x0000,
        fields=[
            _field(15, 12, "reserved", "Reserved"),
            _field(11, 0, "adc_threshold_l", "ADC_Threshold_L"),
        ],
    ),
    RegisterDefinition(
        address=0x0A,
        width=16,
        summary="ADC calibration",
        default_value=0x0000,
        fields=[
            _field(15, 15, "adc_calibe", "ADC_CALIBE", "Toggle 0->1 in IDLE state to auto-calibrate ADC.", choices=BOOL_CHOICES),
            _field(14, 0, "reserved", "Reserved"),
        ],
    ),
    RegisterDefinition(
        address=0x0B,
        width=16,
        summary="ADC mode and input event source",
        default_value=0x0000,
        fields=[
            _field(15, 10, "reserved", "Reserved"),
            _field(9, 9, "adc_gain", "ADC Gain", "1 selects 18V range, 0 selects 100V range.", choices=BOOL_CHOICES),
            _field(8, 8, "adc_pol", "ADC POL", "1 samples OUT-, 0 samples OUT+.", choices=BOOL_CHOICES),
            _field(7, 7, "stop_to_adc_over_h", "Stop to ADC Over H", choices=BOOL_CHOICES),
            _field(6, 6, "stop_to_adc_over_l", "Stop to ADC Over L", choices=BOOL_CHOICES),
            _field(5, 5, "stop_to_adc_over_h_posedge", "Stop to H posedge", choices=BOOL_CHOICES),
            _field(4, 4, "stop_to_adc_over_h_negedge", "Stop to H negedge", choices=BOOL_CHOICES),
            _field(3, 3, "stop_to_adc_over_l_posedge", "Stop to L posedge", choices=BOOL_CHOICES),
            _field(2, 2, "stop_to_adc_over_l_negedge", "Stop to L negedge", choices=BOOL_CHOICES),
            _field(
                1,
                0,
                "adc_mode",
                "ADC Mode",
                choices={
                    0x0: "Direct mode",
                    0x1: "INPT mode",
                    0x2: "Auto mode",
                    0x3: "Auto mode",
                },
            ),
        ],
    ),
    RegisterDefinition(
        address=0x0C,
        width=16,
        summary="ADC debounce/filter",
        default_value=0x0000,
        fields=[
            _field(15, 8, "reserved", "Reserved"),
            _field(7, 4, "adc_average_time", "ADC Average Time", choices=ADC_AVERAGE_CHOICES),
            _field(3, 0, "adc_ovv_time", "ADC OVV Time", choices=ADC_OVV_TIME_CHOICES),
        ],
    ),
    _event_register(0x0D, "ADC over high event playback", "adc_over_h_en", "ADC_Over_H_EN"),
    _event_register(0x0E, "ADC over low event playback", "adc_over_l_en", "ADC_Over_L_EN"),
    _event_register(0x0F, "ADC over high posedge event playback", "adc_over_h_posedge_en", "ADC_Over_H_Posedge_EN"),
    _event_register(0x10, "ADC over high negedge event playback", "adc_over_h_negedge_en", "ADC_Over_H_Negedge_EN"),
    _event_register(0x11, "ADC over low posedge event playback", "adc_over_l_posedge_en", "ADC_Over_L_Posedge_EN"),
    _event_register(0x12, "ADC over low negedge event playback", "adc_over_l_negedge_en", "ADC_Over_L_Negedge_EN"),
    RegisterDefinition(
        address=0x13,
        width=16,
        summary="Playback configuration",
        default_value=0x001F,
        fields=[
            _field(15, 15, "reserved", "Reserved"),
            _field(14, 14, "endless", "Endless", choices=BOOL_CHOICES),
            _field(13, 5, "loop_time", "Loop time", "Playback count is loop time + 1."),
            _field(
                4,
                0,
                "wave_number",
                "Wave number",
                "0x00-0x1D select SRAM slices, 0x1E selects serial order, 0x1F selects ROM sine.",
                choices=WAVE_NUMBER_CHOICES,
            ),
        ],
    ),
    RegisterDefinition(
        address=0x14,
        width=16,
        summary="Output and DAC configuration",
        default_value=0x0000,
        fields=[
            _field(15, 14, "slow_up", "Slow up time"),
            _field(13, 12, "slow_down", "Slow down time"),
            _field(11, 11, "half_cycle", "Half cycle EN", choices=BOOL_CHOICES),
            _field(10, 10, "phase", "Phase", choices={0: "0 degrees", 1: "180 degrees"}),
            _field(9, 7, "data_depth", "Date deep", choices=DATA_DEPTH_CHOICES),
            _field(6, 2, "output_vpk", "OUT Vpk", "Output amplitude code. Vpk = 2.97 * (1 + OUT Vpk)."),
            _field(1, 0, "dac_mode", "DAC mode", choices=DAC_MODE_CHOICES),
        ],
    ),
    RegisterDefinition(
        address=0x15,
        width=16,
        summary="DAC sample rate",
        default_value=0x0000,
        fields=[_field(15, 0, "dac_sample_rate", "DAC Sample Rate", "Playback point time is 1 + DAC Sample Rate us.")],
    ),
    RegisterDefinition(
        address=0x16,
        width=16,
        summary="RAM address",
        default_value=0x0000,
        fields=[
            _field(15, 10, "reserved", "Reserved"),
            _field(9, 0, "ram_address", "RAM Address"),
        ],
    ),
    _serial_register(0x17, "Serial playback pair 0/1", 1, 0),
    _serial_register(0x18, "Serial playback pair 2/3", 3, 2),
    _serial_register(0x19, "Serial playback pair 4/5", 5, 4),
    _serial_register(0x1A, "Serial playback pair 6/7", 7, 6),
    RegisterDefinition(
        address=0x1E,
        width=16,
        summary="Chip ID",
        access=RegisterAccess.READ_ONLY,
        default_value=0x5081,
        fields=[_field(15, 0, "chip_id", "Chip ID")],
    ),
)

AWP192X_DETAIL_REGISTER_BY_ADDRESS = {
    definition.address: definition for definition in AWP192X_DETAIL_REGISTER_DEFINITIONS
}


def format_awp192x_detail_register_option(definition: RegisterDefinition) -> str:
    return f"0x{definition.address:02X} - {definition.summary}"


def awp192x_detail_register_options() -> list[str]:
    return [
        format_awp192x_detail_register_option(definition)
        for definition in sorted(AWP192X_DETAIL_REGISTER_DEFINITIONS, key=lambda item: item.address)
    ]


def parse_awp192x_detail_register_option(text: str) -> int:
    match = re.search(r"0x([0-9a-fA-F]{1,2})", text)
    if match:
        return int(match.group(1), 16) & 0xFF
    return int(text.strip(), 0) & 0xFF


def get_awp192x_detail_register_definition(address: int) -> RegisterDefinition | None:
    return AWP192X_DETAIL_REGISTER_BY_ADDRESS.get(int(address) & 0xFF)
