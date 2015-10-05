#!/usr/bin/env python3

import glob
import json
import numpy as np
import os
import pprint
import sys

import dataprint

if len(sys.argv) != 2:
	print('Need to pass condensed calibration file as first argument.')
	sys.exit(1)

condensed = sys.argv[1]
outfilename_base = os.path.splitext(condensed)[0]

meta = os.path.splitext(condensed)[0] + '.meta'
meta = json.loads(open(meta).read())

SPEED_OF_LIGHT = 299792458
DWT_TIME_UNITS = (1.0/499.2e6/128.0) #!< = 15.65e-12 s

CALIBRATION_NUM_NODES=3
CALIB_NUM_ANTENNAS=3
CALIB_NUM_CHANNELS=3


def dwtime_to_dist(dwtime):
	dist = dwtime * DWT_TIME_UNITS * SPEED_OF_LIGHT;
	#dist += ANCHOR_CAL_LEN;
	#dist -= txDelayCal[anchor_id*NUM_CHANNELS + subseq_num_to_chan(subseq, true)];
	return dist;

def dist_to_dwtime(dist):
	dwtime = dist / (DWT_TIME_UNITS * SPEED_OF_LIGHT)
	return dwtime

# Distance in dwtime between tags
l = dist_to_dwtime(0.15)

def sub_dw_ts(a,b):
	if b > a:
		print(b)
		print(a)
		raise
	return a-b

out = [
		('round', 'calibration', 'deltaB', 'epsilonC', 'converted'),
		]

# { 'NodeA': {(ant,ch): cal, (ant,ch): cal, ...}, 'NodeB': ... }
calibration = {'A': {}, 'B': {}, 'C': {}}

for line in open(condensed):
	try:
		round_num, node, letters = line.split(maxsplit=2)
		round_num = int(round_num)
		L, M, N, O, P, Q = map(int, letters.split())
	except ValueError:
		continue

	antenna = int(round_num / CALIBRATION_NUM_NODES) % CALIB_NUM_ANTENNAS
	channel = int(int(round_num / CALIBRATION_NUM_NODES) / CALIB_NUM_ANTENNAS) % CALIB_NUM_CHANNELS
	node_cal = calibration[node]
	if (antenna,channel) not in node_cal:
		node_cal[(antenna,channel)] = []

	k = sub_dw_ts(Q,O) / sub_dw_ts(P,N)
	deltaB = sub_dw_ts(O,L)
	epsilonC = sub_dw_ts(N,M)

	cal = deltaB - epsilonC * k - l

	node_cal[(antenna,channel)].append(cal)


# http://stackoverflow.com/questions/11686720/is-there-a-numpy-builtin-to-reject-outliers-from-a-list
def reject_outliers(data, m=2.):
	d = np.abs(data - np.median(data))
	mdev = np.median(d)
	s = d/mdev if mdev else 0.
	return data[s<m]

for node in ('A', 'B', 'C'):
	for conf in calibration[node]:
		cal = np.array(calibration[node][conf])
		rej = reject_outliers(cal)

		if (np.std(rej) > 1000):
			print('WARN: Bad calibration for node {} conf {}'.format(node,conf))
			print(cal)
			print(rej)
			calibration[node][conf] = -1
		else:
			calibration[node][conf] = np.mean(rej)

pprint.pprint(calibration)

outdata = []
header = ['Node ID',]
header.extend(map(str, calibration['A'].keys()))
outdata.append(header)

for node in (('A','0'), ('B','1'), ('C','2')):
	row = [meta[node[1]],]
	row.extend(calibration[node[0]].values())
	outdata.append(row)

dataprint.to_newfile(outfilename_base+'.calibration', outdata, overwrite=True)
