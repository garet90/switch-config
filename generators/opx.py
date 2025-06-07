from argparse import ArgumentParser
import yaml
import os
import subprocess
import socket
import struct
import cps
import cps_utils
import nas_os_utils

#class FakeSubprocess:
#    def run(self, command):
#        print(command)
#
#subprocess = FakeSubprocess()

parser = ArgumentParser()
parser.add_argument('-f', '--file', dest='file', help='Network schema file')
parser.add_argument('-d', '--down', dest='down', action='store_true', help='Only bring down interfaces')
args = parser.parse_args()

# bring down previous interfaces (and vlans)
for interface_name in os.listdir('/sys/class/net'):
    if interface_name in ['dummy0', 'npu-0', 'lo']:
        continue

    print("bringing down interface " + interface_name + "...")
    subprocess.check_call(['ip', 'link', 'set', 'dev', interface_name, 'down'])

    # check if this is a tagged vlan
    if len(interface_name.split('.')) == 2:
        print("deleting interface " + interface_name + "...")
        subprocess.check_call(['ip', 'link', 'delete', interface_name])
        continue

    # check if this is a bridge, if so delete it
    process = subprocess.Popen(['brctl', 'show', interface_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if stderr.decode() == "":
        lines = stdout.decode().splitlines()
        for line in lines[1:]:
            member_interface_fields = line.split()
            if len(member_interface_fields) != 4:
                continue
            member_interface_name = member_interface_fields[3]
            print("deleting interface " + member_interface_name + " on vlan " + interface_name + "...")
            subprocess.check_call(['brctl', 'delif', interface_name, member_interface_name])
        print("deleting vlan " + interface_name + "...")
        subprocess.check_call(['brctl', 'delbr', interface_name])

# bring down acl table
for table_name in ['switch-config-ingress']:
    print("deleting acls for table " + table_name + "...")

    f = cps_utils.CPSObject(module='base-acl/entry')
    f.add_attr('table-name', table_name)
    l = []
    cps.get([f.get()], l)
    for obj in l:
        cps_obj = cps_utils.CPSObject(module='base-acl/entry', obj=obj)
        print("deleting acl entry table-name=" + table_name + " id=" + str(cps_obj.get_attr_data('base-acl/entry/id')) + "...")
        if not cps_utils.CPSTransaction([('delete', cps_obj.get())]).commit():
            raise RuntimeError('Error deleting ACL entry')

    f = cps_utils.CPSObject(module='base-acl/table')
    f.add_attr('name', table_name)
    l = []
    cps.get([f.get()], l)
    for obj in l:
        cps_obj = cps_utils.CPSObject(module='base-acl/table', obj=obj)
        print("deleting acl table name=" + table_name + " id=" + str(cps_obj.get_attr_data('base-acl/table/id')) + "...")
        if not cps_utils.CPSTransaction([('delete', cps_obj.get())]).commit():
            raise RuntimeError('Error deleting ACL table')

if args.down:
    exit(0)

if args.file is None:
    parser.print_usage()
    exit(1)

with open(args.file) as f:
    schema = yaml.safe_load(f)

# create vlans
if 'vlan' in schema:
    for vlan_name in schema['vlan']:
        vlan = schema['vlan'][vlan_name]
        assert('id' in vlan)

        print("creating vlan " + vlan_name + "...")
        subprocess.check_call(['brctl', 'addbr', vlan_name])

        # set ip if present
        if 'ip' in vlan:
            print("adding ip address on vlan " + vlan_name + "...")
            subprocess.check_call(['ip', 'addr', 'add', vlan['ip'], 'dev', vlan_name])

        print("bringing up vlan " + vlan_name + "...")
        subprocess.check_call(['ip', 'link', 'set', 'dev', vlan_name, 'up'])

# add routes
if 'route' in schema:
    for network_mask in schema['route']:
        gateway_ip = schema['route'][network_mask]

        print("adding route " + network_mask + " via " + gateway_ip + "...")
        subprocess.check_call(['ip', 'route', 'add', network_mask, 'via', gateway_ip])

# handle interfaces
if 'interface' in schema:
    for interface_name in schema['interface']:
        interface = schema['interface'][interface_name]

        if 'vlan' in interface:
            interface_vlan = interface['vlan']
            if 'untagged' in interface_vlan:
                print("adding untagged vlan " + interface_vlan['untagged'] + " on interface " + interface_name + "...")
                subprocess.check_call(['brctl', 'addif', interface_vlan['untagged'], interface_name])
                subprocess.check_call(['ip', 'link', 'set', 'dev', interface_name, 'up'])

            tagged = []
            if 'tagged' in interface_vlan:
                if interface_vlan['tagged'] == 'all':
                    if 'vlan' in schema:
                        for vlan_name in schema['vlan']:
                            tagged.append(vlan_name)
                else:
                    tagged = interface_vlan['tagged']

            for vlan_name in tagged:
                assert('vlan' in schema)
                assert(vlan_name in schema['vlan'])
                vlan = schema['vlan'][vlan_name]

                tagged_interface_name = interface_name + '.' + str(vlan["id"])
                print("adding tagged vlan " + vlan_name + " on interface " + tagged_interface_name + "...")
                subprocess.check_call(['ip', 'link', 'add', 'link', interface_name, 'name', tagged_interface_name, 'type', 'vlan', 'id', str(vlan['id'])])
                subprocess.check_call(['brctl', 'addif', vlan_name, tagged_interface_name])
                subprocess.check_call(['ip', 'link', 'set', 'dev', tagged_interface_name, 'up'])

            print("bringing up interface " + interface_name + "...")
            subprocess.check_call(['ip', 'link', 'set', 'dev', interface_name, 'up'])

if 'acl' in schema:
    # set up acl
    e_stg = {'INGRESS': 1}
    e_ftype = {'SRC_IP': 5, 'DST_IP': 6}
    e_atype = {'PACKET_ACTION': 3}
    e_ptype = {'DROP': 1}

    # inform CPS utility about the type of each attribute
    type_map = {
        'base-acl/entry/SRC_IP_VALUE/addr': 'ipv4',
        'base-acl/entry/SRC_IP_VALUE/mask': 'ipv4',
        'base-acl/entry/DST_IP_VALUE/addr': 'ipv4',
        'base-acl/entry/DST_IP_VALUE/mask': 'ipv4',
    }
    for key, val in type_map.items():
        cps_utils.cps_attr_types_map.add_type(key, val)

    # create acl table
    print("creating acl table...")
    cps_obj = cps_utils.CPSObject(module='base-acl/table')
    cps_obj.add_attr('name', 'switch-config-ingress')
    cps_obj.add_attr('stage', e_stg['INGRESS'])
    cps_obj.add_attr('priority', 99)
    cps_obj.add_list('allowed-match-fields', [e_ftype['SRC_IP'], e_ftype['DST_IP']])
    r = cps_utils.CPSTransaction([('create', cps_obj.get())]).commit()
    if not r:
        raise RuntimeError("Error creating ACL Table")
    acl_table_id = cps_utils.CPSObject(module='base-acl/table', obj=r[0]['change']).get_attr_data('base-acl/table/id')
    print("created acl table with id=" + str(acl_table_id))

    # create vlan drop lists
    assert('vlan' in schema) # vlan is required to use acls
    vlan_drop_list = set([])
    for vlan_a in schema['vlan']:
        for vlan_b in schema['vlan']:
            if vlan_a == vlan_b:
                continue
            vlan_drop_list.add(frozenset([vlan_a, vlan_b]))

    # iterate through acls and remove allowed entries
    for [a, b] in schema['acl']:
        def to_vlans(spec):
            result = []
            if spec == "all-vlan":
                for vlan_name in schema['vlan']:
                    result.append(vlan_name)
            elif isinstance(spec, dict) and "vlan" in spec:
                result.append(spec["vlan"])
            else:
                raise RuntimeError("Invalid acl spec: " + str(spec))

            return result

        vlans_a = to_vlans(a)
        vlans_b = to_vlans(b)
        for vlan_a in vlans_a:
            for vlan_b in vlans_b:
                vlan_drop_list.discard(frozenset([vlan_a, vlan_b]))

    def cidr_to_netmask(cidr):
        network, net_bits = cidr.split('/')
        host_bits = 32 - int(net_bits)
        netmask = socket.inet_ntoa(struct.pack('!I', (1 << 32) - (1 << host_bits)))
        return network, netmask

    def add_vlan_drop_acl(src_vlan_name, dst_vlan_name):
        print("creating acl to drop traffic from " + src_vlan_name + " to " + dst_vlan_name + "...")
        cps_obj = cps_utils.CPSObject(module='base-acl/entry')
        cps_obj.add_attr('table-id', acl_table_id)
        cps_obj.add_attr('table-name', 'switch-config-ingress')
        cps_obj.add_attr('priority', 512)

        # match src ip
        assert(src_vlan_name in schema['vlan'])
        assert('ip' in schema['vlan'][src_vlan_name])
        src_ip_addr, src_ip_mask = cidr_to_netmask(schema['vlan'][src_vlan_name]['ip'])
        cps_obj.add_embed_attr(['match', '0', 'type'], e_ftype['SRC_IP'])
        cps_obj.add_embed_attr(['match', '0', 'SRC_IP_VALUE', 'addr'], src_ip_addr, 2)
        cps_obj.add_embed_attr(['match', '0', 'SRC_IP_VALUE', 'mask'], src_ip_mask, 2)

        # match dst ip
        assert(dst_vlan_name in schema['vlan'])
        assert('ip' in schema['vlan'][dst_vlan_name])
        dst_ip_addr, dst_ip_mask = cidr_to_netmask(schema['vlan'][dst_vlan_name]['ip'])
        cps_obj.add_embed_attr(['match', '1', 'type'], e_ftype['DST_IP'])
        cps_obj.add_embed_attr(['match', '1', 'DST_IP_VALUE', 'addr'], dst_ip_addr, 2)
        cps_obj.add_embed_attr(['match', '1', 'DST_IP_VALUE', 'mask'], dst_ip_mask, 2)

        # drop packet
        cps_obj.add_embed_attr(['action', '0', 'type'], e_atype['PACKET_ACTION'])
        cps_obj.add_embed_attr(['action', '0', 'PACKET_ACTION_VALUE'], e_ptype['DROP'])

        if not cps_utils.CPSTransaction([('create', cps_obj.get())]).commit():
            raise RuntimeError("Error creating ACL Entry")

    for [vlan_a, vlan_b] in vlan_drop_list:
        add_vlan_drop_acl(vlan_a, vlan_b)
        add_vlan_drop_acl(vlan_b, vlan_a)
