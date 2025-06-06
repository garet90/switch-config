from argparse import ArgumentParser
import yaml
import os
import subprocess

#class FakeSubprocess:
#    def run(self, command):
#        print(command)
#
#subprocess = FakeSubprocess()

parser = ArgumentParser()
parser.add_argument('-f', '--file', dest='file', help='Network schema file')
parser.add_argument('-d', '--down', dest='down', action='store_true', help='Only bring down interfaces')
args = parser.parse_args()

# bring down previous config
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

        # TODO add acls

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
        if interface == 'ignore':
            continue

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
