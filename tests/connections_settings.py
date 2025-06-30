import dbus

ETH0_DBUS_SETTINGS = dbus.Dictionary(
    {
        "connection": dbus.Dictionary(
            {
                "id": dbus.String("wb-eth0", variant_level=1),
                "interface-name": dbus.String("eth0", variant_level=1),
                "type": "802-3-ethernet",
                "uuid": dbus.String("91f1c71d-2d97-4675-886f-ecbe52b8451e", variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
        "ipv4": dbus.Dictionary(
            {"method": dbus.String("auto", variant_level=1)},
            signature=dbus.Signature("sv"),
        ),
    },
    signature=dbus.Signature("sa{sv}"),
)

ETH1_DBUS_SETTINGS = dbus.Dictionary(
    {
        "connection": dbus.Dictionary(
            {
                "id": dbus.String("wb-eth1", variant_level=1),
                "interface-name": dbus.String("eth1", variant_level=1),
                "type": "802-3-ethernet",
                "uuid": dbus.String("c3e38405-9c17-4155-ad70-664311b49066", variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
        "ipv4": dbus.Dictionary(
            {"method": dbus.String("auto", variant_level=1)},
            signature=dbus.Signature("sv"),
        ),
    },
    signature=dbus.Signature("sa{sv}"),
)

GSM_SIM1_DBUS_SETTINGS = dbus.Dictionary(
    {
        "connection": dbus.Dictionary(
            {
                "autoconnect": dbus.Boolean(False, variant_level=1),
                "id": dbus.String("wb-gsm-sim1", variant_level=1),
                "type": "gsm",
                "uuid": dbus.String("5d4297ba-c319-4c05-a153-17cb42e6e196", variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
        "gsm": dbus.Dictionary(
            {
                "auto-config": dbus.Boolean(True, variant_level=1),
                "sim-slot": dbus.Int32(1, variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
        "ipv4": dbus.Dictionary(
            {"method": dbus.String("auto", variant_level=1)},
            signature=dbus.Signature("sv"),
        ),
    },
    signature=dbus.Signature("sa{sv}"),
)

GSM_SIM2_DBUS_SETTINGS = dbus.Dictionary(
    {
        "connection": dbus.Dictionary(
            {
                "autoconnect": dbus.Boolean(False, variant_level=1),
                "id": dbus.String("wb-gsm-sim2", variant_level=1),
                "type": "gsm",
                "uuid": dbus.String("8b9964d4-b8dd-34d3-a3ed-481840bcf8c9", variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
        "gsm": dbus.Dictionary(
            {
                "auto-config": dbus.Boolean(True, variant_level=1),
                "sim-slot": dbus.Int32(2, variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
        "ipv4": dbus.Dictionary(
            {"method": dbus.String("auto", variant_level=1)},
            signature=dbus.Signature("sv"),
        ),
    },
    signature=dbus.Signature("sa{sv}"),
)

WB_AP_DBUS_SETTINGS = dbus.Dictionary(
    {
        "connection": dbus.Dictionary(
            {
                "id": dbus.String("wb-ap", variant_level=1),
                "interface-name": dbus.String("wlan0", variant_level=1),
                "type": "802-11-wireless",
                "uuid": dbus.String("d12c8d3c-1abe-4832-9b71-4ed6e3c20885", variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
        "802-11-wireless": dbus.Dictionary(
            {
                "mode": dbus.String("ap", variant_level=1),
                "ssid": dbus.ByteArray(bytes("WirenBoard-Тест", encoding="utf8")),
                "hidden": dbus.Boolean(False, variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
        "ipv4": dbus.Dictionary(
            {
                "address-data": dbus.Array(
                    [
                        dbus.Dictionary(
                            {
                                "address": dbus.String("192.168.42.1", variant_level=1),
                                "prefix": dbus.Int32(24, variant_level=1),
                            },
                            signature=dbus.Signature("sv"),
                        )
                    ],
                    signature=dbus.Signature("a{sv}"),
                ),
                "method": dbus.String("shared", variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
    },
    signature=dbus.Signature("sa{sv}"),
)
