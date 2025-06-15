#!/usr/bin/python

import argparse
import yaml
import subprocess
import os

parser = argparse.ArgumentParser()
parser.add_argument('-f', '--file', dest='file', help='Network schema file')
parser.add_argument('-d', '--down', dest='down', action='store_true', help='Only bring down interfaces')
args = parser.parse_args()

# bring down vlans
for interface_name in os.listdir('/sys/class/net'):
	if not interface_name.startswith('br'):
		continue

	print('bringing down interface ' + interface_name + '...')
	subprocess.check_call(['ip', 'link', 'set', 'dev', interface_name, 'down'])

	vlan_id = int(interface_name[2:])

	print('deleting vlan with id ' + str(vlan_id) + '...')
	subprocess.check_call(['cps_config_vlan.py', '--del', '--name', interface_name])

# bring down interfaces
for interface_name in os.listdir('/sys/class/net'):
	if interface_name in ['dummy0', 'npu-0', 'lo']:
		continue

	print('bringing down interface ' + interface_name + '...')
	subprocess.check_call(['ip', 'link', 'set', 'dev', interface_name, 'down'])

	if '.' in interface_name:
	        print("deleting interface " + interface_name + "...")
	        subprocess.check_call(['ip', 'link', 'delete', interface_name])

if args.down:
	exit(0)

if args.file is None:
	parser.print_usage()
	exit(1)

with open(args.file) as f:
	schema = yaml.safe_load(f)

if 'vlan' in schema:
	for vlan_name in schema['vlan']:
		vlan_schema = schema['vlan'][vlan_name]
		vlan_id = vlan_schema['id']

		print('creating vlan ' + vlan_name + ' with id ' + str(vlan_id) + '...')
		subprocess.check_call(['cps_config_vlan.py', '--add', '--id', str(vlan_id), '--vlantype', '1'])
		vlan_interface_name = 'br' + str(vlan_id)

		# disable multicast snooping (there is a bug that causes segfaults)
		with open('/sys/class/net/' + vlan_interface_name + '/bridge/multicast_snooping', 'w') as file:
			file.write('0')

		# enable proxy arp (needed for inter vlan routing)
		with open('/proc/sys/net/ipv4/conf/' + vlan_interface_name + '/proxy_arp', 'w') as file:
			file.write('1')

		print('bringing up vlan with id ' + str(vlan_id) + '...')
		subprocess.check_call(['ip', 'link', 'set', 'dev', vlan_interface_name, 'up'])

		if 'ip' in vlan_schema:
			vlan_ip = vlan_schema['ip']
			print('adding ip ' + vlan_ip + ' to vlan with id ' + str(vlan_id) + '...')
			subprocess.check_call(['ip', 'addr', 'add', vlan_ip, 'dev', vlan_interface_name])

# add routes
if 'route' in schema:
	for network_mask in schema['route']:
		gateway_ip = schema['route'][network_mask]

		print("adding route " + network_mask + " via " + gateway_ip + "...")
		subprocess.check_call(['ip', 'route', 'add', network_mask, 'via', gateway_ip])

if 'interface' in schema:
	for interface_name in schema['interface']:
		interface_schema = schema['interface'][interface_name]

		# set interface parameters
		if 'connection' in interface_schema:
			interface_connection_schema = interface_schema['connection']

			print('configuring connection for interface ' + interface_name + '...')

			autoneg_values = {'off': 0, 'on': 1}
			autoneg = 'on'
			if 'autoneg' in interface_connection_schema:
				autoneg = interface_connection_schema['autoneg']

			speed_values = {'0M': 0, '10M': 1, '100M': 2, '1G': 3, '10G': 4, '25G': 5, '40G': 6, '100G': 7, 'auto': 8, '20G': 9, '50G': 10, '200G': 11, '400G': 12, '4G-FC': 13, '8G-FC': 14, '16G-FC': 15, '32G-FC': 16, '2G-FC': 17, '64G-FC': 18, '128G-FC': 19, '4G': 20, '1G-FC': 21}
			speed = '0M'
			if 'speed' in interface_connection_schema:
				speed = interface_connection_schema['speed']

			duplex_values = {'full': 1, 'half': 2, 'auto': 3}
			duplex = 'full'
			if 'duplex' in interface_connection_schema:
				duplex = interface_connection_schema['duplex']

			fec_values = {'auto': 1, 'off': 2, 'cl91-rs': 3, 'cl74-fc': 4, 'cl108-rs': 5}
			fec = 'off'
			if 'fec' in interface_connection_schema:
				fec = interface_connection_schema['fec']

			# get interface id
			process = subprocess.Popen(['cps_get_oid.py', 'dell-base-if-cmn/if/interfaces/interface', 'if/interfaces/interface/name=' + interface_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
			stdout, stderr = process.communicate()
			rows = [row.split(' = ') for row in stdout.decode().split('\n')]
			fields = {row[0].strip(): row[1].strip() for row in rows if len(row) == 2}

			subprocess.check_call(['cps_set_oid.py', '-oper=set', 'dell-base-if-cmn/if/interfaces/interface', 'dell-base-if-cmn/if/interfaces/interface/if-index=' + fields['dell-base-if-cmn/if/interfaces/interface/if-index'], 'dell-if/if/interfaces/interface/auto-negotiation=' + str(autoneg_values[autoneg]), 'dell-if/if/interfaces/interface/speed=' + str(speed_values[speed]), 'dell-if/if/interfaces/interface/duplex=' + str(duplex_values[duplex]), 'dell-if/if/interfaces/interface/fec=' + str(fec_values[fec])])

		# add vlans
		if 'vlan' in interface_schema:
			interface_vlan_schema = interface_schema['vlan']

			if 'untagged' in interface_vlan_schema:
				vlan_schema = schema['vlan'][interface_vlan_schema['untagged']]
				vlan_id = vlan_schema['id']
				vlan_interface_name = 'br' + str(vlan_id)

				print('adding interface ' + interface_name + ' to vlan with id ' + str(vlan_id) + '...')
				subprocess.check_call(['cps_config_vlan.py', '--addport', '--name', vlan_interface_name, '--port', interface_name])
				subprocess.check_call(['ip', 'link', 'set', 'dev', interface_name, 'up'])

			tagged = []
			if 'tagged' in interface_vlan_schema:
				if interface_vlan_schema['tagged'] == 'all':
					if 'vlan' in schema:
						for vlan_name in schema['vlan']:
							tagged.append(vlan_name)
				else:
					tagged = interface_vlan_schema['tagged']

			for vlan_name in tagged:
				if 'untagged' in interface_vlan_schema and interface_vlan_schema['untagged'] == vlan_name:
					continue

				vlan_schema = schema['vlan'][vlan_name]
				vlan_id = vlan_schema['id']
				vlan_interface_name = 'br' + str(vlan_id)

				print('adding tagged interface ' + interface_name + ' to vlan with id ' + str(vlan_id) + '...')
				subprocess.check_call(['cps_config_vlan.py', '--addport', '--name', vlan_interface_name, '--port', interface_name, '--tagged'])
