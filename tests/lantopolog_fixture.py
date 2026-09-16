from __future__ import annotations


def export_files(*, include_wide_endpoints: bool = True) -> dict[str, str]:
    files = {
        "sw_list.csv": (
            'N;IP;Model;"Serial number";"MAC address";"SNMP Version";Name;Location;Description\n'
            '1;192.168.1.2;J9299A;SER-1;C09134866580;v2c;Core;Rack;HP Ethernet switch\n'
            '2;192.168.1.3;UDM-Pro;?;5AD61F4505E9;v2c;Gateway;Office;Ubiquiti gateway\n'
        ),
        "sw_conn.csv": (
            "Name;Location;IP;ifName;-;ifName;IP;Name;Location\n"
            "Core;Rack;192.168.1.2;1;-;8;192.168.1.3;Gateway;Office\n"
            "Gateway;Office;192.168.1.3;8;-;1;192.168.1.2;Core;Rack\n"
        ),
        "port_list.csv": (
            'Port;IfIndex;Name;"Admin Status";"Oper Status";Speed;DuplexMode;STPstate;'
            '"Tagged VLAN";"Untagged VLAN";"PVID VLAN";Alias\n'
            '"Switch 192.168.1.2  Core"\n'
            "1;1;1;up;up;1000;Full-Duplex;forwarding;;1;1;Uplink\n"
            '"Switch 192.168.1.3  Gateway"\n'
        ),
        "vlan_list.csv": (
            '"VLAN ID";"VLAN Name";Switch;Tagged(trunk)ports;Untagged(access)ports\n'
            '1;DEFAULT_VLAN;"192.168.1.2  Core";;1..24\n'
        ),
        "complist2.csv": (
            '"Connected to";"Port Name, Alias";MAC;IP;HostName;Domain;UserName;"Custom data";'
            'VLAN;"Port Speed";"MAC Lookup Vendor";"Last discovery date"\n'
            '"sw 192.168.1.2  port 5";"5, Desk";001122334455;192.168.1.20;DESKTOP-1;LOCAL;alex;blue;1;1000;Dell Inc.;08/04/2026\n'
            '"sw 192.168.1.2  port 6";"6, Phone";08000FE62377;;;LOCAL;;;4;100;MITEL CORPORATION;08/04/2026\n'
        ),
        "top_map.xml": (
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="n1" value="192.168.1.2&amp;nbsp; Core" vertex="1" parent="1">'
            '<mxGeometry x="20" y="80" width="50" height="8" as="geometry"/></mxCell>'
            '<mxCell id="n2" value="192.168.1.3&amp;nbsp; Gateway" vertex="1" parent="1">'
            '<mxGeometry x="200" y="160" width="50" height="8" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" source="n1" target="n2" parent="1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="l1" value="1-8" vertex="1" parent="e1"/>'
            '</root></mxGraphModel>'
        ),
        "Tmp/swlist.csv": "N;IP;Model\n1;demo;truncated\n",
    }
    if include_wide_endpoints:
        files["complist.csv"] = (
            'MAC;IP;HostName;Domain;UserName;"MAC Lookup Vendor";"Custom data";"Connected to";'
            '"Port Name, Alias";"Port Speed";"Last discovery date";VLAN;Manufacturer;Model;'
            'TotalPhysicalMemory;"OS Caption"\n'
            '001122334455;192.168.1.20;DESKTOP-1;LOCAL;alex;Dell Inc.;blue;'
            '"sw 192.168.1.2  port 5";"5, Desk";1000;08/04/2026;1;Dell;OptiPlex;17179869184;Windows 11\n'
            '08000FE62377;;;LOCAL;;MITEL CORPORATION;;"sw 192.168.1.2  port 6";'
            '"6, Phone";100;08/04/2026;4;;;;;\n'
        )
    return files
