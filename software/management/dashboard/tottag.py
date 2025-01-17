#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# PYTHON INCLUSIONS ---------------------------------------------------------------------------------------------------

from functools import partial
from bleak import BleakClient, BleakScanner
from tkinter import ttk, filedialog
from collections import defaultdict
import struct, queue, datetime, tzlocal
import os, pickle, pytz
import tkinter as tk
import tkcalendar
import threading
import asyncio


# CONSTANTS AND DEFINITIONS -------------------------------------------------------------------------------------------

DEVICE_ID_UUID = '00002a23-0000-1000-8000-00805f9b34fb'
LOCATION_SERVICE_UUID = 'd68c3156-a23f-ee90-0c45-5231395e5d2e'
FIND_MY_TOTTAG_SERVICE_UUID = 'd68c3155-a23f-ee90-0c45-5231395e5d2e'
TIMESTAMP_SERVICE_UUID = 'd68c3154-a23f-ee90-0c45-5231395e5d2e'
VOLTAGE_SERVICE_UUID = 'd68c3153-a23f-ee90-0c45-5231395e5d2e'
EXPERIMENT_SERVICE_UUID = 'd68c3161-a23f-ee90-0c45-5231395e5d2e'
MAINTENANCE_COMMAND_SERVICE_UUID = 'd68c3162-a23f-ee90-0c45-5231395e5d2e'
MAINTENANCE_DATA_SERVICE_UUID = 'd68c3163-a23f-ee90-0c45-5231395e5d2e'

MAINTENANCE_NEW_EXPERIMENT = 0x01
MAINTENANCE_DELETE_EXPERIMENT = 0x02
MAINTENANCE_DOWNLOAD_LOG = 0x03
MAINTENANCE_DOWNLOAD_COMPLETE = 0xFF

FIND_MY_TOTTAG_ACTIVATION_SECONDS = 10
MAX_LABEL_LENGTH = 16
MAX_NUM_DEVICES = 10

STORAGE_TYPE_VOLTAGE = 1
STORAGE_TYPE_CHARGING_EVENT = 2
STORAGE_TYPE_MOTION = 3
STORAGE_TYPE_RANGES = 4

BATTERY_CODES = defaultdict(lambda: 'Unknown Battery Event')
BATTERY_CODES[1] = 'Plugged'
BATTERY_CODES[2] = 'Unplugged'
BATTERY_CODES[3] = 'Charging'
BATTERY_CODES[4] = 'Not Charging'


# HELPER FUNCTIONS ----------------------------------------------------------------------------------------------------

async def ble_command_sender(message_queue, command):
   await message_queue.put(command)

def ble_issue_command(event_loop, message_queue, command):
   asyncio.run_coroutine_threadsafe(ble_command_sender(message_queue, command), event_loop)

def get_download_directory():
   if os.name == 'nt':
      import winreg
      sub_key = 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Shell Folders'
      downloads_guid = '{374DE290-123F-4565-9164-39C4925E467B}'
      with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
         location = winreg.QueryValueEx(key, downloads_guid)[0]
      return location
   else:
      return os.path.join(os.path.expanduser('~'), 'Downloads')

def validate_time(new_val):
   return (len(new_val) == 0) or \
          (len(new_val) == 1 and new_val.isnumeric()) or \
          (len(new_val) == 2 and (new_val[-1] == ':' or (new_val[-1].isnumeric() and int(new_val) < 24))) or \
          (len(new_val) == 3 and ((new_val[-2] != ':' and new_val[-1] == ':') or (new_val[-2] == ':' and new_val[-1].isnumeric() and int(new_val[-1]) <= 5))) or \
          (len(new_val) == 4 and new_val[-1].isnumeric() and (new_val[-2] != ':' or int(new_val[-1]) <= 5)) or \
          (len(new_val) == 5 and new_val[-1].isnumeric() and new_val[-3] == ':')

def pack_datetime(time_zone, date_string, time_string):
   if date_string:
      utc_datetime = pytz.timezone(time_zone).localize(datetime.datetime.strptime(date_string + ' ' + time_string, '%m/%d/%Y %H:%M')).astimezone(pytz.utc)
      timestamp = int(utc_datetime.timestamp())
   else:
      timestamp = int((datetime.datetime.strptime(time_string, '%H:%M') - datetime.datetime.strptime('00:00', '%H:%M')).total_seconds())
   return timestamp

def unpack_datetime(time_zone, include_date_string, timestamp):
   date_string = None
   if include_date_string:
      local_datetime = pytz.utc.localize(datetime.datetime.utcfromtimestamp(timestamp)).astimezone(pytz.timezone(time_zone))
      date_string = local_datetime.strftime('%m/%d/%Y')
      time_string = local_datetime.strftime('%H:%M')
      seconds_string = local_datetime.strftime('%S')
   else:
      hours = int(timestamp/3600)
      time_string = '%02d:%02d'%(hours, int((timestamp - (hours*3600)) / 60))
      seconds_string = '%02d'%(timestamp % 60)
   return date_string, time_string, seconds_string

def pack_experiment_details(data):
   experiment_struct = struct.pack('<BIIIIB' + ('6B'*MAX_NUM_DEVICES) + ((str(MAX_LABEL_LENGTH)+'s')*MAX_NUM_DEVICES),
                           MAINTENANCE_NEW_EXPERIMENT, data['start_time'], data['end_time'], data['daily_start_time'],
                           data['daily_end_time'], data['num_devices'], *(i for uid in data['uids'] for i in uid), *data['labels'])
   return experiment_struct

def unpack_experiment_details(data):
   experiment_struct = struct.unpack('<IIIIB' + ('6B'*MAX_NUM_DEVICES) + ((str(MAX_LABEL_LENGTH)+'s')*MAX_NUM_DEVICES), data)
   return {
      'start_time': experiment_struct[0],
      'end_time': experiment_struct[1],
      'daily_start_time': experiment_struct[2],
      'daily_end_time': experiment_struct[3],
      'num_devices': experiment_struct[4],
      'uids': [list(experiment_struct[5+i:5+i+6]) for i in range(0, MAX_NUM_DEVICES*6, 6)],
      'labels': experiment_struct[(5+6*MAX_NUM_DEVICES):],
   }

def process_tottag_data(from_uid, storage_directory, details, data):
   uid_to_labels = defaultdict(lambda: 'Unknown')
   for i in range(details['num_devices']):
      label = details['labels'][i].decode().rstrip('\x00')
      uid_to_labels[int(details['uids'][i][0])] = label if label else details['uids'][i][0]
   i = 0
   log_data = defaultdict(dict)
   while i < len(data):
      timestamp = struct.unpack('<I', data[i+1:i+5])
      if data[i] == STORAGE_TYPE_VOLTAGE:
         log_data[timestamp]['v'] = struct.unpack('<I', data[i+5:i+9])
         i += 9
      elif data[i] == STORAGE_TYPE_CHARGING_EVENT:
         log_data[timestamp]['c'] = BATTERY_CODES[data[i+5]]
         i += 6
      elif data[i] == STORAGE_TYPE_MOTION:
         log_data[timestamp]['m'] = data[i+5] > 0
         i += 6
      elif data[i] == STORAGE_TYPE_RANGES:
         log_data[timestamp]['r'] = {}
         for j in range(data[i+5]):
            log_data[timestamp]['r'][uid_to_labels[data[i+6+(j*3)]]] = struct.unpack('<h', data[i+7+(j*3):i+9+(j*3)])
         i += 6 + data[i+5]*3
   log_data = [dict({'t': ts}, **datum) for ts, datum in log_data.items()]
   with open(os.path.join(storage_directory, uid_to_labels[from_uid] + '.pkl'), 'wb') as file:
      pickle.dump(dict(log_data), file, protocol=pickle.HIGHEST_PROTOCOL)


# BLUETOOTH LE COMMUNICATIONS -----------------------------------------------------------------------------------------

class TotTagBLE(threading.Thread):

   def __init__(self, command_queue, result_queue, event_loop):
      super().__init__()
      self.operations = { 'SCAN': self.scan_for_tottags,
                          'CONNECT': self.connect_to_tottag,
                          'DISCONNECT': self.disconnect_from_tottag,
                          'SUBSCRIBE_RANGES': self.subscribe_to_ranges,
                          'FIND_TOTTAG': self.find_my_tottag,
                          'TIMESTAMP': self.retrieve_timestamp,
                          'VOLTAGE': self.retrieve_voltage,
                          'NEW_EXPERIMENT': self.create_new_experiment,
                          'GET_EXPERIMENT': self.retrieve_experiment,
                          'DELETE_EXPERIMENT': self.delete_experiment,
                          'DOWNLOAD': self.download_logs,
                          'DOWNLOAD_DONE': self.download_logs_done }
      self.storage_directory = get_download_directory()
      self.subscribed_to_notifications = False
      self.downloading_log_file = False
      self.command_queue = command_queue
      self.result_queue = result_queue
      self.discovered_devices = {}
      self.connected_device = None
      self.event_loop = event_loop
      self.data_details = None
      self.data_length = 0
      self.data_index = 0
      self.data = None

   def run(self):
      self.event_loop.run_until_complete(self.await_command())

   async def await_command(self):
      command = 'START'
      while command != 'QUIT':
         command = await self.command_queue.get()
         if self.subscribed_to_notifications:
            await self.unsubscribe_from_ranges()
         if self.downloading_log_file:
            await self.download_logs_done()
         if command in self.operations:
            await self.operations[command]()
         else:
            await self.disconnect_from_tottag()
         self.command_queue.task_done()

   def disconnected_callback(self, _device):
      self.result_queue.put_nowait(('DISCONNECTED', True))
      self.connected_device = None

   def ranges_callback(self, _sender_uuid, data):
      self.result_queue.put_nowait(('RANGES', data))

   def data_callback(self, _sender_uuid, data):
      if self.data_length == 0:
         self.data_index = 0
         self.data_details = None
         self.data_length = struct.unpack('<I', data[0:4])[0]
         self.data = bytearray(self.data_length)
         self.result_queue.put_nowait(('LOGDATA', self.data_length))
      elif self.data_details is None:
         self.data_details = unpack_experiment_details(data)
      elif len(data) == 1 and data[0] == MAINTENANCE_DOWNLOAD_COMPLETE:
         self.command_queue.put_nowait('DOWNLOAD_DONE')
      else:
         self.data[self.data_index:self.data_index+len(data)] = data
         self.data_index += len(data)
         self.result_queue.put_nowait(('LOGDATA', self.data_index))

   async def scan_for_tottags(self):
      self.result_queue.put_nowait(('SCANNING', True))
      self.discovered_devices.clear()
      for device_address, device_info in (await BleakScanner.discover(5.0, return_adv=True)).items():
         if device_info[1].local_name == 'TotTag':
            self.discovered_devices[device_address] = device_info[0]
            self.result_queue.put_nowait(('DEVICE', device_address))
      self.result_queue.put_nowait(('SCANNING', False))

   async def connect_to_tottag(self):
      self.result_queue.put_nowait(('CONNECTING', True))
      device_address = await self.command_queue.get()
      device = BleakClient(self.discovered_devices[device_address], partial(self.disconnected_callback))
      try:
         await device.connect(timeout=3.0)
         if device.is_connected:
            self.connected_device = device
            self.result_queue.put_nowait(('CONNECTED', device_address))
      except Exception:
         self.result_queue.put_nowait(('CONNECTING', False))
         self.result_queue.put_nowait(('ERROR', ('TotTag Connection Error', 'Timed out attempting to connect to the specified TotTag')))
      self.command_queue.task_done()

   async def disconnect_from_tottag(self):
      if self.connected_device:
         await self.connected_device.disconnect()

   async def subscribe_to_ranges(self):
      try:
         await self.connected_device.start_notify(LOCATION_SERVICE_UUID, partial(self.ranges_callback))
         self.subscribed_to_notifications = True
      except Exception:
         self.result_queue.put_nowait(('ERROR', ('TotTag Error', 'Unable to subscribe to ranging data from the TotTag')))

   async def unsubscribe_from_ranges(self):
      self.subscribed_to_notifications = False
      try:
         await self.connected_device.stop_notify(LOCATION_SERVICE_UUID)
      except Exception:
         pass

   async def find_my_tottag(self):
      self.result_queue.put_nowait(('RETRIEVING', True))
      try:
         await self.connected_device.write_gatt_char(FIND_MY_TOTTAG_SERVICE_UUID, struct.pack('<I', FIND_MY_TOTTAG_ACTIVATION_SECONDS), True)
         self.result_queue.put_nowait(('RETRIEVING', False))
      except Exception:
         self.result_queue.put_nowait(('ERROR', ('TotTag Error', 'Unable to activate FindMyTotTag')))

   async def retrieve_timestamp(self):
      self.result_queue.put_nowait(('RETRIEVING', True))
      try:
         timestamp = struct.unpack('<I', bytes(await self.connected_device.read_gatt_char(TIMESTAMP_SERVICE_UUID)))[0]
         self.result_queue.put_nowait(('TIMESTAMP', timestamp))
      except Exception:
         self.result_queue.put_nowait(('ERROR', ('TotTag Error', 'Unable to retrieve current time from TotTag')))

   async def retrieve_voltage(self):
      self.result_queue.put_nowait(('RETRIEVING', True))
      try:
         voltage = struct.unpack('<H', bytes(await self.connected_device.read_gatt_char(VOLTAGE_SERVICE_UUID)))[0]
         self.result_queue.put_nowait(('VOLTAGE', voltage))
      except Exception:
         self.result_queue.put_nowait(('ERROR', ('TotTag Error', 'Unable to retrieve current battery level from TotTag')))

   async def create_new_experiment(self):
      success = True
      self.result_queue.put_nowait(('SCHEDULING', True))
      details = await self.command_queue.get()
      packed_details = pack_experiment_details(details)
      for device_id in details['devices']:
         try:
            async with BleakClient(self.discovered_devices[device_id]) as client:
               await client.write_gatt_char(TIMESTAMP_SERVICE_UUID, struct.pack('<I', round(datetime.datetime.now(datetime.timezone.utc).timestamp())), True)
               await client.write_gatt_char(MAINTENANCE_COMMAND_SERVICE_UUID, packed_details, True)
         except Exception:
            self.result_queue.put_nowait(('SCHEDULING_FAILURE', device_id))
            success = False
      self.result_queue.put_nowait(('SCHEDULED', success))
      self.command_queue.task_done()

   async def retrieve_experiment(self):
      self.result_queue.put_nowait(('RETRIEVING', True))
      try:
         details = unpack_experiment_details(bytes(await self.connected_device.read_gatt_char(EXPERIMENT_SERVICE_UUID)))
         self.result_queue.put_nowait(('EXPERIMENT', details))
      except Exception:
         self.result_queue.put_nowait(('ERROR', ('TotTag Error', 'Unable to retrieve scheduled deployment details from TotTag')))

   async def delete_experiment(self):
      self.result_queue.put_nowait(('RETRIEVING', True))
      try:
         await self.connected_device.write_gatt_char(TIMESTAMP_SERVICE_UUID, struct.pack('<I', round(datetime.datetime.now(datetime.timezone.utc).timestamp())), True)
         await self.connected_device.write_gatt_char(MAINTENANCE_COMMAND_SERVICE_UUID, struct.pack('B', MAINTENANCE_DELETE_EXPERIMENT), True)
         self.result_queue.put_nowait(('DELETED', True))
      except Exception:
         self.result_queue.put_nowait(('ERROR', ('TotTag Error', 'Unable to delete scheduled deployment from TotTag')))

   async def download_logs(self):
      self.storage_directory = await self.command_queue.get()
      try:
         self.data_length = 0
         await self.connected_device.start_notify(MAINTENANCE_DATA_SERVICE_UUID, partial(self.data_callback))
         await self.connected_device.write_gatt_char(MAINTENANCE_COMMAND_SERVICE_UUID, struct.pack('B', MAINTENANCE_DOWNLOAD_LOG), True)
         self.downloading_log_file = True
      except Exception:
         await self.connected_device.stop_notify(MAINTENANCE_DATA_SERVICE_UUID)
         self.result_queue.put_nowait(('ERROR', ('TotTag Error', 'Unable to retrieve log files from the TotTag')))
      self.command_queue.task_done()

   async def download_logs_done(self):
      self.downloading_log_file = False
      try:
         self.result_queue.put_nowait(('DOWNLOADED', True))
         await self.connected_device.stop_notify(MAINTENANCE_DATA_SERVICE_UUID)
         if self.data_index == self.data_length:
            process_tottag_data(int(self.connected_device.address.split[':'][-1]), self.storage_directory, self.data_details, self.data)
      except Exception:
         self.result_queue.put_nowait(('ERROR', ('TotTag Error', 'Unable to write log file to ' + self.storage_directory)))


# GUI DESIGN ----------------------------------------------------------------------------------------------------------

class TotTagGUI(tk.Frame):

   def __init__(self):

      # Set up the root application window
      super().__init__(None)
      self.master.title('TotTag Dashboard')
      try: self.master.iconbitmap('dashboard/tottag_dashboard.ico')
      except Exception: self.master.iconbitmap('tottag_dashboard.ico')
      self.master.protocol('WM_DELETE_WINDOW', self._exit)
      self.master.geometry("900x700+" + str((self.winfo_screenwidth()-900)//2) + "+" + str((self.winfo_screenheight()-700)//2))
      self.pack(fill=tk.BOTH, expand=True)

      # Create an asynchronous event loop
      self.event_loop = asyncio.new_event_loop()
      asyncio.set_event_loop(self.event_loop)

      # Create all necessary shared variables
      self.device_list = []
      self.failed_devices = []
      self.use_daily_times = tk.IntVar()
      self.ble_command_queue = asyncio.Queue()
      self.ble_result_queue = queue.Queue()
      self.tottag_selection = tk.StringVar(self.master, 'Press "Scan for TotTags" to begin...')
      self.tottag_timezone = tk.StringVar(self.master, tzlocal.get_localzone())
      self.save_directory = tk.StringVar(self.master, get_download_directory())
      self.daily_start_time = tk.StringVar(self.master, "07:00")
      self.daily_end_time = tk.StringVar(self.master, "22:00")
      self.start_time = tk.StringVar(self.master, "07:00")
      self.end_time = tk.StringVar(self.master, "22:00")
      self.start_date = tk.StringVar()
      self.end_date = tk.StringVar()
      self.data_length = 0

      # Create the control bar
      control_bar = tk.Frame(self)
      control_bar.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5, expand=False)
      control_bar.columnconfigure(1, weight=1)
      self.scan_button = ttk.Button(control_bar, text="Scan for TotTags", command=partial(ble_issue_command, self.event_loop, self.ble_command_queue, 'SCAN'), width=20)
      self.scan_button.grid(column=0, row=0, padx=(10,0))
      self.tottag_selector = ttk.Combobox(control_bar, textvariable=self.tottag_selection, state=['readonly'])
      self.tottag_selector.grid(column=1, row=0, padx=10, sticky=tk.W+tk.E)
      self.connect_button = ttk.Button(control_bar, text="Connect", command=self._connect, state=['disabled'])
      self.connect_button.grid(column=2, row=0)
      ttk.Button(control_bar, text="Quit", command=self._exit).grid(column=3, row=0)

      # Create the operations bar
      self.operations_bar = tk.Frame(self)
      self.operations_bar.pack(side=tk.LEFT, fill=tk.Y, padx=5, expand=False)
      ttk.Label(self.operations_bar, text="TotTag Actions", padding=6).grid(row=0)
      ttk.Button(self.operations_bar, text="Subscribe to Live Ranging Data", command=self._subscribe_to_live_ranges, state=['disabled']).grid(row=1, sticky=tk.W+tk.E)
      ttk.Button(self.operations_bar, text="Activate Find my TotTag", command=partial(ble_issue_command, self.event_loop, self.ble_command_queue, 'FIND_TOTTAG'), state=['disabled']).grid(row=2, sticky=tk.W+tk.E)
      ttk.Button(self.operations_bar, text="Retrieve Current Timestamp", command=partial(ble_issue_command, self.event_loop, self.ble_command_queue, 'TIMESTAMP'), state=['disabled']).grid(row=3, sticky=tk.W+tk.E)
      ttk.Button(self.operations_bar, text="Retrieve Battery Voltage", command=partial(ble_issue_command, self.event_loop, self.ble_command_queue, 'VOLTAGE'), state=['disabled']).grid(row=4, sticky=tk.W+tk.E)
      self.schedule_button = ttk.Button(self.operations_bar, text="Schedule New Pilot Deployment", command=self._create_new_experiment, state=['disabled'])
      self.schedule_button.grid(row=5, sticky=tk.W+tk.E)
      ttk.Button(self.operations_bar, text="Get Scheduled Deployment Details", command=partial(ble_issue_command, self.event_loop, self.ble_command_queue, 'GET_EXPERIMENT'), state=['disabled']).grid(row=6, sticky=tk.W+tk.E)
      ttk.Button(self.operations_bar, text="Cancel Scheduled Pilot Deployment", command=self._delete_experiment, state=['disabled']).grid(row=7, sticky=tk.W+tk.E)
      ttk.Button(self.operations_bar, text="Download Deployment Logs", command=self._download_logs, state=['disabled']).grid(row=8, sticky=tk.W+tk.E)

      # Create the workspace canvas
      self.canvas = tk.Frame(self)
      self.canvas.pack(fill=tk.BOTH, padx=(0, 5), pady=(0, 5), expand=True)
      tk.Label(self.canvas, text="Scan for TotTag devices to continue...").pack(fill=tk.BOTH, expand=True)

      # Start the BLE communications thread
      self.ble_comms = TotTagBLE(self.ble_command_queue, self.ble_result_queue, self.event_loop)
      self.ble_comms.daemon = True
      self.ble_comms.start()

      # Start a timer to refresh UI data every 100ms
      self.master.after(100, self._refresh_data)

   def _exit(self):
      self._clear_canvas()
      tk.Label(self.canvas, text="Shutting down...").pack(fill=tk.BOTH, expand=True)
      ble_issue_command(self.event_loop, self.ble_command_queue, 'QUIT')
      self.ble_comms.join()
      self.master.destroy()

   def _connect(self):
      ble_issue_command(self.event_loop, self.ble_command_queue, 'CONNECT')
      ble_issue_command(self.event_loop, self.ble_command_queue, self.tottag_selection.get())

   def _delete_experiment(self):
      self._clear_canvas()
      prompt_area = tk.Frame(self.canvas)
      prompt_area.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
      tk.Label(prompt_area, text="Are you sure you want to cancel the currently scheduled deployment?").grid(column=0, row=0, columnspan=4, sticky=tk.W+tk.E+tk.N+tk.S)
      ttk.Button(prompt_area, text="Yes", command=partial(ble_issue_command, self.event_loop, self.ble_command_queue, 'DELETE_EXPERIMENT')).grid(column=1, row=1)
      ttk.Button(prompt_area, text="No", command=partial(self._clear_canvas_with_prompt)).grid(column=2, row=1)

   def _download_logs(self):
      self._clear_canvas()
      prompt_area = tk.Frame(self.canvas)
      prompt_area.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
      tk.Label(prompt_area, text="Download Deployment Log Files").grid(column=0, row=0, columnspan=4, sticky=tk.W+tk.E+tk.N+tk.S)
      ttk.Label(prompt_area, text=" ").grid(column=0, row=1)
      self.progress_label = ttk.Label(prompt_area, text="Current Progress: 0%")
      self.progress_label.grid(column=0, row=2, columnspan=2, sticky=tk.W)
      self.progress_bar = ttk.Progressbar(prompt_area, mode='determinate', orient=tk.HORIZONTAL, length=400)
      self.progress_bar.grid(column=0, row=3, columnspan=4)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '10')).grid(column=0, row=4)
      save_controls = tk.Frame(prompt_area)
      save_controls.grid(column=0, row=5, columnspan=4, sticky=tk.W+tk.E+tk.N+tk.S)
      ttk.Label(save_controls, text="Saving to: ").pack(side=tk.LEFT)
      ttk.Button(save_controls, text="Change", command=self._change_save_directory).pack(side=tk.RIGHT)
      ttk.Entry(save_controls, textvariable=self.save_directory).pack(fill=tk.X)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '4')).grid(column=0, row=6)
      def begin_download(self):
         self.data_length = 0
         ble_issue_command(self.event_loop, self.ble_command_queue, 'DOWNLOAD')
         ble_issue_command(self.event_loop, self.ble_command_queue, self.save_directory.get())
      ttk.Button(prompt_area, text="Begin", command=partial(begin_download, self)).grid(column=1, row=7)
      ttk.Button(prompt_area, text="Cancel", command=partial(self._clear_canvas_with_prompt)).grid(column=2, row=7)

   def _create_new_experiment(self):
      self._clear_canvas()
      self.tottag_rows = []
      prompt_area = tk.Frame(self.canvas)
      prompt_area.place(relx=0.5, rely=0, anchor=tk.N)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '4')).grid(column=0, row=0)
      tk.Label(prompt_area, text="Schedule New Pilot Deployment").grid(column=0, row=1, columnspan=5, sticky=tk.W+tk.E+tk.N+tk.S)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '4')).grid(column=0, row=2)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '2')).grid(column=0, row=4)
      ttk.Label(prompt_area, text="Deployment Timezone:").grid(column=0, row=5, columnspan=2, sticky=tk.W)
      ttk.Combobox(prompt_area, textvariable=self.tottag_timezone, values=pytz.all_timezones, state=['readonly']).grid(column=2, row=5, columnspan=3, sticky=tk.W+tk.E)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '2')).grid(column=0, row=6)
      tk.Label(prompt_area, text="Start Date").grid(column=0, row=7, sticky=tk.W)
      tk.Label(prompt_area, text="Start Time").grid(column=1, row=7, sticky=tk.W)
      tk.Label(prompt_area, text="             ").grid(column=2, row=7)
      tk.Label(prompt_area, text="End Date").grid(column=3, row=7, sticky=tk.W)
      tk.Label(prompt_area, text="End Time").grid(column=4, row=7, sticky=tk.W)
      tkcalendar.DateEntry(prompt_area, textvariable=self.start_date, selectmode='day', firstweekday='sunday', showweeknumbers=False, date_pattern='mm/dd/yyyy').grid(column=0, row=8, sticky=tk.W)
      ttk.Entry(prompt_area, textvariable=self.start_time, width=10, validate='all', validatecommand=(prompt_area.register(validate_time), '%P')).grid(column=1, row=8, sticky=tk.W)
      tkcalendar.DateEntry(prompt_area, textvariable=self.end_date, selectmode='day', firstweekday='sunday', showweeknumbers=False, date_pattern='mm/dd/yyyy').grid(column=3, row=8, sticky=tk.W)
      ttk.Entry(prompt_area, textvariable=self.end_time, width=10, validate='all', validatecommand=(prompt_area.register(validate_time), '%P')).grid(column=4, row=8, sticky=tk.W)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '4')).grid(column=0, row=9)
      daily_label_start = tk.Label(prompt_area, text="Daily Start Time", state=['normal' if self.use_daily_times.get() else 'disabled'])
      daily_label_start.grid(column=0, row=10, columnspan=2, sticky=tk.W)
      daily_label_end = tk.Label(prompt_area, text="Daily End Time", state=['normal' if self.use_daily_times.get() else 'disabled'])
      daily_label_end.grid(column=3, row=10, columnspan=2, sticky=tk.W)
      daily_entry_start = ttk.Entry(prompt_area, textvariable=self.daily_start_time, width=10, validate='all', validatecommand=(prompt_area.register(validate_time), '%P'), state=['normal' if self.use_daily_times.get() else 'disabled'])
      daily_entry_start.grid(column=0, row=11, sticky=tk.W)
      daily_entry_end = ttk.Entry(prompt_area, textvariable=self.daily_end_time, width=10, validate='all', validatecommand=(prompt_area.register(validate_time), '%P'), state=['normal' if self.use_daily_times.get() else 'disabled'])
      daily_entry_end.grid(column=3, row=11, sticky=tk.W)
      def change_daily_entries_state(self):
         daily_label_start['state'] = ['normal' if self.use_daily_times.get() else 'disabled']
         daily_label_end['state'] = ['normal' if self.use_daily_times.get() else 'disabled']
         daily_entry_start['state'] = ['normal' if self.use_daily_times.get() else 'disabled']
         daily_entry_end['state'] = ['normal' if self.use_daily_times.get() else 'disabled']
      ttk.Checkbutton(prompt_area, text="Include daily start/end times", variable=self.use_daily_times, command=partial(change_daily_entries_state, self)).grid(column=0, row=3, columnspan=5, sticky=tk.W)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '12')).grid(column=0, row=12)
      ttk.Separator(prompt_area, orient='horizontal').grid(column=0, row=13, columnspan=5, sticky=tk.W+tk.E)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '8')).grid(column=0, row=14)
      ttk.Label(prompt_area, text="TotTags in Deployment:").grid(column=0, row=15, columnspan=3, sticky=tk.W)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '4')).grid(column=0, row=16)
      def remove_tottag(self, row):
         row.destroy()
         self.tottag_rows.remove(row)
         for idx in range(len(self.tottag_rows)):
            self.tottag_rows[idx].grid(row=17+idx, column=0, columnspan=5, sticky=tk.W+tk.E)
      def add_tottag(self):
         if len(self.tottag_rows) < MAX_NUM_DEVICES:
            row = tk.Frame(prompt_area)
            tottag_label = tk.StringVar(row)
            row.grid(row=17+len(self.tottag_rows), column=0, columnspan=5, sticky=tk.W+tk.E)
            tottag_selector = ttk.Combobox(row, width=18, values=self.device_list, state=['readonly'])
            tottag_selector.pack(side=tk.LEFT, expand=False)
            tottag_selector.set(self.device_list[0])
            ttk.Label(row, text="  using label  ").pack(side=tk.LEFT, expand=False)
            ttk.Entry(row, textvariable=tottag_label, validate='all', validatecommand=(row.register(lambda new_val: len(new_val) <= MAX_LABEL_LENGTH), '%P')).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Label(row, text="  ").pack(side=tk.LEFT, expand=False)
            ttk.Button(row, text="Remove", command=partial(remove_tottag, self, row)).pack(side=tk.LEFT, expand=False)
            self.tottag_rows.append(row)
      def construct_details(self):
         devices = []
         labels = [b''] * MAX_NUM_DEVICES
         uids = [[0 for _ in range(6)] for _ in range(MAX_NUM_DEVICES)]
         for i, row in enumerate(self.tottag_rows):
            for child in row.winfo_children():
               if isinstance(child, ttk.Combobox):
                  devices.append(child.get())
                  for j, grouping in enumerate(child.get().split(':')[::-1]):
                     uids[i][j] = int(grouping, 16)
               elif isinstance(child, ttk.Entry):
                  labels[i] = bytes(child.get(), 'utf-8')
         chosen_tags, chosen_labels, errors = [], [], False
         for i in range(len(self.tottag_rows)):
            if errors:
               break
            if uids[i] in chosen_tags:
               errors = True
               tk.messagebox.showerror('TotTag Error', 'ERROR: You have chosen more than one of the same TotTag!')
            else:
               chosen_tags.append(uids[i])
            if not errors and len(labels[i]) > 0:
               if labels[i] in chosen_labels:
                  errors = True
                  tk.messagebox.showerror('TotTag Error', 'ERROR: You have chosen the same label for multiple TotTags!')
               else:
                  chosen_labels.append(labels[i])
         if not errors:
            details = {
               'start_time': pack_datetime(self.tottag_timezone.get(), self.start_date.get(), self.start_time.get()),
               'end_time': pack_datetime(self.tottag_timezone.get(), self.end_date.get(), self.end_time.get()),
               'daily_start_time': pack_datetime(self.tottag_timezone.get(), None, self.daily_start_time.get()) if self.use_daily_times.get() else 0,
               'daily_end_time': pack_datetime(self.tottag_timezone.get(), None, self.daily_end_time.get()) if self.use_daily_times.get() else 0,
               'num_devices': len(self.tottag_rows),
               'uids': uids,
               'labels': labels,
               'devices': devices
            }
            ble_issue_command(self.event_loop, self.ble_command_queue, 'NEW_EXPERIMENT')
            ble_issue_command(self.event_loop, self.ble_command_queue, details)
      ttk.Button(prompt_area, text="Add", command=partial(add_tottag, self)).grid(column=4, row=15, sticky=tk.E)
      ttk.Label(prompt_area, text=" ", font=('Helvetica', '4')).grid(column=0, row=99)
      ttk.Button(prompt_area, text="Schedule", command=partial(construct_details, self)).grid(column=1, row=100)
      ttk.Button(prompt_area, text="Cancel", command=partial(self._clear_canvas_with_prompt)).grid(column=3, row=100)
      add_tottag(self)

   def _show_experiment(self, data):
      self._clear_canvas()
      uids, labels = [], []
      start_date_deployment, start_time_deployment, _ = unpack_datetime(self.tottag_timezone.get(), True, data['start_time'])
      end_date_deployment, end_time_deployment, _ = unpack_datetime(self.tottag_timezone.get(), True, data['end_time'])
      start_date_local, start_time_local, _ = unpack_datetime(tzlocal.get_localzone_name(), True, data['start_time'])
      end_date_local, end_time_local, _ = unpack_datetime(tzlocal.get_localzone_name(), True, data['end_time'])
      start_date_utc, start_time_utc, _ = unpack_datetime('UTC', True, data['start_time'])
      end_date_utc, end_time_utc, _ = unpack_datetime('UTC', True, data['end_time'])
      daily_start_time = unpack_datetime(None, False, data['daily_start_time'])[1] if data['daily_start_time'] > 0 else None
      daily_end_time = unpack_datetime(None, False, data['daily_end_time'])[1] if data['daily_end_time'] > 0 else None
      for i in range(data['num_devices']):
         uids.append('%02X:%02X:%02X:%02X:%02X:%02X'%tuple(data['uids'][i][::-1]))
         labels.append(data['labels'][i].decode().rstrip('\x00'))
      area = tk.Frame(self.canvas)
      self.start_datetime_deployment = tk.StringVar(area, start_date_deployment + ' ' + start_time_deployment)
      self.end_datetime_deployment = tk.StringVar(area, end_date_deployment + ' ' + end_time_deployment)
      area.place(relx=0.5, rely=0, anchor=tk.N)
      ttk.Label(area, text=" ", font=('Helvetica', '4')).grid(column=0, row=0)
      tk.Label(area, text="Pilot Deployment Details").grid(column=0, row=1, columnspan=5, sticky=tk.W+tk.E+tk.N+tk.S)
      ttk.Label(area, text=" ", font=('Helvetica', '6')).grid(column=0, row=2)
      ttk.Label(area, text="Select Deployment Timezone:").grid(column=0, row=3, columnspan=2, sticky=tk.E)
      def change_deployment_timezone(self, _event, start_time, end_time):
         start_date_deployment, start_time_deployment, _ = unpack_datetime(self.tottag_timezone.get(), True, start_time)
         end_date_deployment, end_time_deployment, _ = unpack_datetime(self.tottag_timezone.get(), True, end_time)
         self.start_datetime_deployment.set(start_date_deployment + ' ' + start_time_deployment)
         self.end_datetime_deployment.set(end_date_deployment + ' ' + end_time_deployment)
      tz_selection = ttk.Combobox(area, textvariable=self.tottag_timezone, values=pytz.all_timezones, state=['readonly'])
      tz_selection.grid(column=2, row=3, columnspan=3, sticky=tk.W+tk.E)
      tz_selection.bind("<<ComboboxSelected>>", lambda event, st=data['start_time'], et=data['end_time']: change_deployment_timezone(self, event, st, et))
      ttk.Label(area, text=" ", font=('Helvetica', '6')).grid(column=0, row=4)
      tk.Label(area, text="Start Date and Time (UTC)").grid(column=0, row=5, columnspan=2, sticky=tk.W)
      tk.Label(area, text="             ").grid(column=2, row=5)
      tk.Label(area, text="End Date and Time (UTC)").grid(column=3, row=5, columnspan=2, sticky=tk.W)
      tk.Label(area, text=start_date_utc + ' ' + start_time_utc).grid(column=0, row=6, columnspan=2, sticky=tk.W)
      tk.Label(area, text="             ").grid(column=2, row=6)
      tk.Label(area, text=end_date_utc + ' ' + end_time_utc).grid(column=3, row=6, columnspan=2, sticky=tk.W)
      ttk.Label(area, text=" ", font=('Helvetica', '4')).grid(column=0, row=7)
      tk.Label(area, text="Start Date and Time (Local)").grid(column=0, row=8, columnspan=2, sticky=tk.W)
      tk.Label(area, text="             ").grid(column=2, row=8)
      tk.Label(area, text="End Date and Time (Local)").grid(column=3, row=8, columnspan=2, sticky=tk.W)
      tk.Label(area, text=start_date_local + ' ' + start_time_local).grid(column=0, row=9, columnspan=2, sticky=tk.W)
      tk.Label(area, text="             ").grid(column=2, row=9)
      tk.Label(area, text=end_date_local + ' ' + end_time_local).grid(column=3, row=9, columnspan=2, sticky=tk.W)
      ttk.Label(area, text=" ", font=('Helvetica', '4')).grid(column=0, row=10)
      tk.Label(area, text="Start Date and Time (Deployment)").grid(column=0, row=11, columnspan=2, sticky=tk.W)
      tk.Label(area, text="             ").grid(column=2, row=11)
      tk.Label(area, text="End Date and Time (Deployment)").grid(column=3, row=11, columnspan=2, sticky=tk.W)
      tk.Label(area, textvariable=self.start_datetime_deployment).grid(column=0, row=12, columnspan=2, sticky=tk.W)
      tk.Label(area, text="             ").grid(column=2, row=12)
      tk.Label(area, textvariable=self.end_datetime_deployment).grid(column=3, row=12, columnspan=2, sticky=tk.W)
      if daily_start_time and daily_end_time:
         ttk.Label(area, text=" ", font=('Helvetica', '4')).grid(column=0, row=13)
         tk.Label(area, text="Daily Start Time").grid(column=0, row=14, columnspan=2, sticky=tk.W)
         tk.Label(area, text="             ").grid(column=2, row=14)
         tk.Label(area, text="Daily End Time").grid(column=3, row=14, columnspan=2, sticky=tk.W)
         tk.Label(area, text=daily_start_time).grid(column=0, row=15, columnspan=2, sticky=tk.W)
         tk.Label(area, text="             ").grid(column=2, row=15)
         tk.Label(area, text=daily_end_time).grid(column=3, row=15, columnspan=2, sticky=tk.W)
      ttk.Label(area, text=" ", font=('Helvetica', '12')).grid(column=0, row=16)
      ttk.Separator(area, orient='horizontal').grid(column=0, row=17, columnspan=5, sticky=tk.W+tk.E)
      ttk.Label(area, text=" ", font=('Helvetica', '8')).grid(column=0, row=18)
      ttk.Label(area, text="TotTags in Deployment:").grid(column=0, row=19, columnspan=3, sticky=tk.W)
      ttk.Label(area, text=" ", font=('Helvetica', '4')).grid(column=0, row=20)
      for i in range(len(uids)):
         ttk.Label(area, text='        ' + uids[i] + ': ' + (labels[i] if labels[i] else '<unlabeled>')).grid(row=21+i, column=0, columnspan=5, sticky=tk.W+tk.E)

   def _subscribe_to_live_ranges(self):
      self._clear_canvas()
      scroll_area = tk.Frame(self.canvas)
      scroll_area.pack(fill=tk.BOTH, expand=True)
      scroll_area.rowconfigure(0, weight=1)
      scroll_area.columnconfigure(0, weight=1)
      self.txt_area = tk.Text(scroll_area, highlightthickness=0, takefocus=0, undo=False, state=tk.DISABLED)
      self.txt_area.grid(row=0, column=0, sticky=tk.N+tk.S+tk.E+tk.W)
      scrollbar = ttk.Scrollbar(scroll_area, command=self.txt_area.yview)
      scrollbar.grid(row=0, column=1, sticky=tk.N+tk.S+tk.E+tk.W)
      self.txt_area['yscrollcommand'] = scrollbar.set
      ble_issue_command(self.event_loop, self.ble_command_queue, 'SUBSCRIBE_RANGES')

   def _range_received(self, data):
      self.txt_area['state'] = tk.NORMAL
      txt_string = 'Ranges to %d devices:\n'%data[0]
      for i in range(data[0]):
         txt_string += '   0x%02X: %d mm\n'%(data[(3*i)+1], struct.unpack('<H', data[(3*i)+2:(3*i)+4])[0])
      self.txt_area.insert(tk.INSERT, txt_string)
      self.txt_area.see(tk.END)
      self.txt_area['state'] = tk.DISABLED

   def _log_data_received(self, data_length):
      if self.data_length == 0:
         self.data_length = data_length
         self.progress_bar['maximum'] = data_length
         data_length = 0
      self.progress_bar['value'] = data_length
      self.progress_label['text'] = 'Current Progress: %d%%'%(int(100.0 * (data_length / self.data_length)))

   def _refresh_data(self):
      while not self.ble_result_queue.empty():
         key, data = self.ble_result_queue.get()
         if key == 'ERROR':
            tk.messagebox.showerror(data[0], data[1])
         elif key == 'SCANNING':
            self._clear_canvas()
            if data:
               self.device_list.clear()
               self.scan_button['state'] = ['disabled']
               self.schedule_button['state'] = ['disabled']
               self.tottag_selection.set('Scanning for TotTags...')
               tk.Label(self.canvas, text="Scanning for TotTag devices. Please wait...").pack(fill=tk.BOTH, expand=True)
            else:
               self.scan_button['state'] = ['enabled']
               if len(self.device_list) == 0:
                  self.tottag_selection.set('No TotTags found!')
                  tk.Label(self.canvas, text="No TotTag devices found!").pack(fill=tk.BOTH, expand=True)
               else:
                  self.connect_button['state'] = ['enabled']
                  self.schedule_button['state'] = ['enabled']
                  self.tottag_selector['values'] = self.device_list
                  self.tottag_selection.set(self.device_list[0])
                  tk.Label(self.canvas, text="Connect to a TotTag from the list above to continue...").pack(fill=tk.BOTH, expand=True)
         elif key == 'DEVICE':
            self.device_list.append(data)
         elif key == 'CONNECTING':
            self._clear_canvas()
            if data:
               self.scan_button['state'] = ['disabled']
               self.schedule_button['state'] = ['disabled']
               tk.Label(self.canvas, text="Connecting to TotTag with ID "+self.tottag_selection.get()).pack(fill=tk.BOTH, expand=True)
            else:
               self.scan_button['state'] = ['enabled']
               self.schedule_button['state'] = ['enabled']
               tk.Label(self.canvas, text="Connect to a TotTag from the list above to continue...").pack(fill=tk.BOTH, expand=True)
         elif key == 'CONNECTED':
            self.tottag_selection.set('Connected to ' + data)
            self.connect_button['command'] = partial(ble_issue_command, self.event_loop, self.ble_command_queue, 'DISCONNECT')
            self.connect_button['state'] = ['enabled']
            self.connect_button['text'] = 'Disconnect'
            for item in self.operations_bar.winfo_children():
               if isinstance(item, ttk.Button):
                  item.configure(state=['enabled'])
            self.schedule_button['state'] = ['disabled']
            self._clear_canvas_with_prompt()
         elif key == 'DISCONNECTED':
            self._clear_canvas()
            self.device_list.clear()
            self.scan_button['state'] = ['enabled']
            self.tottag_selector['values'] = self.device_list
            self.connect_button['command'] = self._connect
            self.connect_button['state'] = ['disabled']
            self.connect_button['text'] = 'Connect'
            self.tottag_selection.set('Press "Scan for TotTags" to begin...')
            for item in self.operations_bar.winfo_children():
               if isinstance(item, ttk.Button):
                  item.configure(state=['disabled'])
            tk.Label(self.canvas, text="Scan for TotTag devices to continue...").pack(fill=tk.BOTH, expand=True)
         elif key == 'RETRIEVING':
            self._clear_canvas()
            if data:
               tk.Label(self.canvas, text="Carrying out the selected operation. Please wait...").pack(fill=tk.BOTH, expand=True)
            else:
               tk.Label(self.canvas, text="Operation complete!").pack(fill=tk.BOTH, expand=True)
         elif key == 'TIMESTAMP':
            self._clear_canvas()
            date_string_utc, time_string_utc, _ = unpack_datetime('UTC', True, data)
            date_string_local, time_string_local, seconds = unpack_datetime(tzlocal.get_localzone_name(), True, data)
            tk.Label(self.canvas, text="Current Device Timestamp (UTC): {} {}:{}\nCurrent Device Timestamp (Local): {} {}:{}".format(date_string_utc, time_string_utc, seconds, date_string_local, time_string_local, seconds)).pack(fill=tk.BOTH, expand=True)
         elif key == 'VOLTAGE':
            self._clear_canvas()
            tk.Label(self.canvas, text="Current Device Voltage: {} mV".format(data)).pack(fill=tk.BOTH, expand=True)
         elif key == 'SCHEDULING':
            self._clear_canvas()
            self.failed_devices.clear()
            tk.Label(self.canvas, text="Scheduling Pilot Deployment, please wait...").pack(fill=tk.BOTH, expand=True)
         elif key == 'SCHEDULING_FAILURE':
            self.failed_devices.append(data)
         elif key == 'SCHEDULED':
            self._clear_canvas()
            if len(self.failed_devices) > 0:
               text = "Pilot Deployment was successfully scheduled!"
            else:
               text = "Pilot Deployment was NOT successfully scheduled!\n\nCould not communicate with:\n"
               for device_id in self.failed_devices:
                  text += device_id + "\n"
            tk.Label(self.canvas, text=text).pack(fill=tk.BOTH, expand=True)
         elif key == 'EXPERIMENT':
            self._show_experiment(data)
         elif key == 'DELETED':
            self._clear_canvas()
            tk.Label(self.canvas, text="Deployment was successfully canceled!").pack(fill=tk.BOTH, expand=True)
         elif key == 'RANGES':
            self._range_received(data)
         elif key == 'LOGDATA':
            self._log_data_received(data)
         elif key == 'DOWNLOADED':
            self._clear_canvas()
            tk.Label(self.canvas, text="Download complete! Your files were saved to:\n\n"+self.save_directory.get()).pack(fill=tk.BOTH, expand=True)
         else:
            print('Unrecognized BLE Data:', key, '=', data)
      if self.ble_comms.is_alive():
         self.master.after(100, self._refresh_data)

   def _clear_canvas(self):
      for item in self.canvas.winfo_children():
         item.destroy()

   def _clear_canvas_with_prompt(self):
      self._clear_canvas()
      tk.Label(self.canvas, text="Select a TotTag Action from the left to continue...").pack(fill=tk.BOTH, expand=True)

   def _change_save_directory(self):
      new_directory = filedialog.askdirectory(parent=self, title='Choose TotTag Storage Directory', initialdir=self.save_directory.get())
      if new_directory:
         self.save_directory.set(new_directory)


# TOP-LEVEL FUNCTIONALITY ---------------------------------------------------------------------------------------------

def main():
   gui = TotTagGUI()
   gui.mainloop()

if __name__ == "__main__":
   main()
