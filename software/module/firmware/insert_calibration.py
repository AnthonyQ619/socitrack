#!/usr/bin/env python3

import struct
import sys

CALIBRATIONS_FNAME = '../../calibration/module_calibration.data'
OUTPUT_FNAME	   = '_build/calibration.bin'

FLASH_LOCATION = '0x0803FF00'

MAGIC_VALUE = 0x77AA38F9

DEFAULT_CALIB = 33000

if len(sys.argv) < 2:
	print('Must pass ID to {}'.format(sys.argv[0]), file=sys.stderr)
	sys.exit(1)
elif len(sys.argv) < 3:
	print('Must pass EUI to {}'.format(sys.argv[0]), file=sys.stderr)
	sys.exit(1)

ID  = sys.argv[1]

# Get the EUI for the device
EUI = int(sys.argv[2], 16)
print(EUI, file=sys.stderr)

# Get calibration data
with open(CALIBRATIONS_FNAME) as f:
	for l in f:
		values = l.split()

		if values[0] == ID:
			# Found the calibration values
			calib_values = values[1:]
			# Check if there were calibration values we couldn't get.
			# If so, use the default value.
			for i in range(len(calib_values)):
				calib_values[i] = int(calib_values[i])
				if calib_values[i] == -1:
					calib_values[i] = DEFAULT_CALIB
			break
	else:
		print('Did not find calibration values for {}'.format(ID), file=sys.stderr)
		print('Using default value ({})'.format(DEFAULT_CALIB), file=sys.stderr)
		calib_values = [DEFAULT_CALIB] * 9

print(calib_values, file=sys.stderr)

# Create a binary file that can be loaded into the flash
with open(OUTPUT_FNAME, 'wb') as f:
	# Create the buffer to write: 1 Long (4 bytes), 1 Long Long (8 bytes), 9 Shorts (2 bytes)
	b = struct.pack('<LQ9H', MAGIC_VALUE, EUI, *calib_values)
	f.write(b)

print('loadbin {} {}'.format(OUTPUT_FNAME, FLASH_LOCATION))
