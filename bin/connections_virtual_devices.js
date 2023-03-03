var connectionUuids = [];
var controlMetaTopicList = ['type', 'order', 'readonly'];
var controlTopicList = [
  'Name',
  'UUID',
  'Type',
  'Active',
  'Device',
  'State',
  'Address',
  'Connectivity',
  'UpDown',
];
var deviceMetaTopicList = ['name', 'driver'];

function getVirtualDeviceName(connectionUuid) {
  return 'system__networks__' + connectionUuid;
}

function defineNewDevice(connectionName, connectionUuid, connectionType) {
  return defineVirtualDevice(getVirtualDeviceName(connectionUuid), {
    title: 'Network Connection ' + connectionName,
    cells: {
      Name: { order: 1, title: 'Name', type: 'text', value: connectionName },
      UUID: { order: 2, title: 'UUID', type: 'text', value: connectionUuid },
      Type: { order: 3, title: 'Type', type: 'text', value: connectionType },
      Active: { order: 4, title: 'Active', type: 'switch', value: false, readonly: true },
      Device: { order: 5, title: 'Device', type: 'text', value: '' },
      State: { order: 6, title: 'State', type: 'text', value: '' },
      Address: { order: 7, title: 'IP', type: 'text', value: '' },
      Connectivity: {
        order: 8,
        title: 'Connectivity',
        type: 'switch',
        value: false,
        readonly: true,
      },
      UpDown: {
        order: 9,
        title: 'Up',
        type: 'pushbutton',
        value: 1,
        readonly: false,
      },
    },
  });
}

function deleteDevice(connectionUuid) {
  for (var i = 0; i < controlTopicList.length; i++) {
    for (var j = 0; j < controlMetaTopicList.length; j++) {
      var topic =
        '/devices/' +
        getVirtualDeviceName(connectionUuid) +
        '/controls/' +
        controlTopicList[i] +
        '/meta/' +
        controlMetaTopicList[j];
      publish(topic, '');
    }
    var topic =
      '/devices/' + getVirtualDeviceName(connectionUuid) + '/controls/' + controlTopicList[i];
    publish(topic, '');
  }
  for (var i = 0; i < deviceMetaTopicList.length; i++) {
    var topic =
      '/devices/' + getVirtualDeviceName(connectionUuid) + '/meta/' + deviceMetaTopicList[i];
    publish(topic, '');
  }
  var topic = '/devices/' + getVirtualDeviceName(connectionUuid);
  publish(topic, '');
}

function deleteOldDevices(newConnectionUuids) {
  var difference = connectionUuids.filter(function (value) {
    return newConnectionUuids.indexOf(value) == -1;
  });
  for (var i = 0; i < difference.length; i++) {
    deleteDevice(difference[i]);
  }
}

function updateDeviceControl(mqttConnectionDevice, controlName, value) {
  var oldValue = mqttConnectionDevice.getControl(controlName).getValue() || '';
  if (oldValue != value) {
    mqttConnectionDevice.getControl(controlName).setValue(value);
  }
}

function updateDeviceData(mqttConnectionDevice, device, active, state) {
  updateDeviceControl(mqttConnectionDevice, 'Device', device);
  updateDeviceControl(mqttConnectionDevice, 'Active', active);
  updateDeviceControl(mqttConnectionDevice, 'State', state);
}

function updateIp(mqttConnectionDevice) {
  var uuid = mqttConnectionDevice.getControl('UUID').getValue();

  runShellCommand('nmcli -g ip4.address c s ' + uuid, {
    captureOutput: true,
    exitCallback: function (exitCode, capturedOutput) {
      var ipList = capturedOutput.split('|');
      var title = '';
      for (var i = 0; i < ipList.length; i++) {
        title = title.concat(ipList[i].replace(new RegExp('/[0-9]+'), ' '));
      }
      mqttConnectionDevice.getControl('Address').setValue(title);
    },
  });
}

function updateConnectivity(mqttConnectionDevice) {
  var uuid = mqttConnectionDevice.getControl('UUID').getValue();

  runShellCommand(
    'ping -q -W1 -c3 -I $(nmcli -g GENERAL.IP-IFACE  connection show ' +
      uuid +
      ') 1.1.1.1 2>/dev/null',
    {
      captureOutput: false,
      exitCallback: function (exitCode) {
        mqttConnectionDevice.getControl('Connectivity').setValue(exitCode === 0);
      },
    }
  );
}

function updateNetworking(mqttConnectionDevice) {
  updateConnectivity(mqttConnectionDevice);
  updateIp(mqttConnectionDevice);
}

function updateUpDownButton(mqttConnectionDevice, activeFlag) {
  var title = activeFlag ? 'Down' : 'Up';
  mqttConnectionDevice.getControl('UpDown').setTitle(title);
}

function disableUpDownButton(mqttConnectionDevice) {
  mqttConnectionDevice.getControl('UpDown').setReadonly(true);
}

function getUpDownCommand(mqttConnectionDevice) {
  var buttonTitle = mqttConnectionDevice.getControl('UpDown').getTitle();
  var uuid = mqttConnectionDevice.getControl('UUID').getValue();

  if (buttonTitle == 'Up') {
    return 'nmcli connection up ' + uuid;
  } else {
    return 'nmcli connection down ' + uuid;
  }
}

function enableUpDownButton(mqttConnectionDevice) {
  var uuid = mqttConnectionDevice.getControl('UUID').getValue();
  runShellCommand('LC_ALL=C nmcli -g uuid,device,active,state c s | grep ' + uuid + ' ', {
    captureOutput: true,
    exitCallback: function (exitCode, capturedOutput) {
      var dataList = capturedOutput.replace(/\n/, '').split(':');
      var device = dataList[1] || '';
      var active = dataList[2] == 'yes' ? true : false;
      var state = dataList[3] || '';

      // we should publicate this at the end of up/down process in any case
      mqttConnectionDevice.getControl('Device').setValue(device);
      mqttConnectionDevice.getControl('Active').setValue(active);
      mqttConnectionDevice.getControl('State').setValue(state);
      mqttConnectionDevice.getControl('UpDown').setReadonly(false);
    },
  });
}

function defineNewRules(mqttConnectionDevice) {
  defineRule('whenUpDown' + mqttConnectionDevice.getId(), {
    whenChanged: mqttConnectionDevice.getId() + '/UpDown',
    then: function (newValue, devName, cellName) {
      disableUpDownButton(mqttConnectionDevice);
      runShellCommand(getUpDownCommand(mqttConnectionDevice), {
        captureOutput: false,
        exitCallback: function (exitCode) {
          enableUpDownButton(mqttConnectionDevice);
        },
      });
    },
  });

  defineRule('whenStateCnanged' + mqttConnectionDevice.getId(), {
    whenChanged: mqttConnectionDevice.getId() + '/State',
    then: function (newValue, devName, cellName) {
      var timerName = 'updateNetwork' + mqttConnectionDevice.getId();
      if (newValue == 'activated') {
        startTicker(timerName, 60000);
      } else {
        timers[timerName].stop();
      }
      updateNetworking(mqttConnectionDevice);
    },
  });

  defineRule('whenActiveCnanged' + mqttConnectionDevice.getId(), {
    whenChanged: mqttConnectionDevice.getId() + '/Active',
    then: function (newValue, devName, cellName) {
      updateUpDownButton(mqttConnectionDevice, newValue);
    },
  });

  defineRule('whenUpdateMoment' + mqttConnectionDevice.getId(), {
    when: function () {
      return timers['updateNetwork' + mqttConnectionDevice.getId()].firing;
    },
    then: function () {
      updateNetworking(mqttConnectionDevice);
    },
  });
}

function initializeOldDevices() {
  trackMqtt('/devices/+/controls/UUID', function (message) {
    var regex = new RegExp(/^\/devices\/system__networks__(.*)\/controls\/UUID/);
    oldUuid = regex.exec(message.topic)[1];

    if (message.value === '') {
      var index = connectionUuids.indexOf(oldUuid);
      if (index > -1) {
        connectionUuids.splice(index, 1);
      }
      return;
    }

    if (connectionUuids.indexOf(oldUuid) == -1) {
      connectionUuids.push(oldUuid);
    }
  });
}

function initializeDevices() {
  runShellCommand('LC_ALL=C nmcli -g name,uuid,type,active  c s', {
    captureOutput: true,
    exitCallback: function (exitCode, capturedOutput) {
      var connectionsList = capturedOutput.split(/\r?\n/);
      for (var i = 0; i < connectionsList.length - 1; i++) {
        var dataList = connectionsList[i].replace(/\n/, '').split(':');
        var name = dataList[0];
        var uuid = dataList[1];
        var type = dataList[2];
        var active = dataList[3] == 'yes' ? true : false;

        var newMqttConnectionDevice = defineNewDevice(name, uuid, type);
        defineNewRules(newMqttConnectionDevice);
        updateUpDownButton(newMqttConnectionDevice, active);
      }
    },
  });
}

function updateDevices() {
  runShellCommand('LC_ALL=C nmcli -g name,uuid,type,device,active,state c s', {
    captureOutput: true,
    exitCallback: function (exitCode, capturedOutput) {
      if (exitCode == 0) {
        var uuidList = [];
        var connectionsList = capturedOutput.split(/\r?\n/);

        for (var i = 0; i < connectionsList.length - 1; i++) {
          var dataList = connectionsList[i].replace(/\n/, '').split(':');
          var name = dataList[0];
          var uuid = dataList[1];
          var type = dataList[2];
          var device = dataList[3] || '';
          var active = dataList[4] == 'yes' ? true : false;
          var state = dataList[5] || '';
          uuidList.push(uuid);

          var mqttConnectionDevice = getDevice(getVirtualDeviceName(uuid));
          if (mqttConnectionDevice == undefined) {
            mqttConnectionDevice = defineNewDevice(name, uuid, type);
            defineNewRules(mqttConnectionDevice);
          }
          updateDeviceData(mqttConnectionDevice, device, active, state);
        }

        deleteOldDevices(uuidList);
      }
    },
  });
}

initializeOldDevices();
initializeDevices();
setInterval(updateDevices, 2000);
