function getVirtualDeviceName(connectionUuid) {
  return 'system__networks__' + connectionUuid;
}

function defineNewDevice(connectionName, connectionUuid, connectionType) {
  defineVirtualDevice(getVirtualDeviceName(connectionUuid), {
    title: 'Network Connection ' + connectionName,
    cells: {
      UUID: { order: 1, title: 'UUID', type: 'text', value: connectionUuid },
      Type: { order: 2, title: 'Type', type: 'text', value: connectionType },
      Active: { order: 3, title: 'Active', type: 'switch', value: false, readonly: true },
      Device: { order: 4, title: 'Device', type: 'text', value: '' },
      State: { order: 5, title: 'State', type: 'text', value: '' },
      Address: { order: 6, title: 'IP', type: 'text', value: '' },
      Connectivity: {
        order: 7,
        title: 'Connectivity',
        type: 'switch',
        value: false,
        readonly: true,
      },
      Enable: {
        order: 8,
        title: 'Up',
        type: 'pushbutton',
        value: 1,
      },
      Disable: {
        order: 9,
        title: 'Down',
        type: 'pushbutton',
        value: 1,
      },
    },
  });
}

function updateIp(connectionName, connectionUuid) {
  runShellCommand('nmcli -g ip4.address c s ' + connectionName, {
    captureOutput: true,
    exitCallback: function (exitCode, capturedOutput) {
      getDevice(getVirtualDeviceName(connectionUuid))
        .getControl('Address')
        .setValue(capturedOutput);
    },
  });
}

function updateConnectivity(connectionUuid, connectionDevice) {
  runShellCommand('ping -q -W1 -c3 -I ' + connectionDevice + ' 1.1.1.1 2>/dev/null', {
    captureOutput: false,
    exitCallback: function (exitCode) {
      getDevice(getVirtualDeviceName(connectionUuid))
        .getControl('Connectivity')
        .setValue(exitCode === 0);
    },
  });
}

function updateNetworking(connectionName, connectionUuid, connectionDevice) {
  updateConnectivity(connectionUuid, connectionDevice);
  updateIp(connectionName, connectionUuid);
}

function defineNewRules(connectionName, connectionUuid, connectionDevice) {
  defineRule('whenEnable' + connectionUuid, {
    whenChanged: getVirtualDeviceName(connectionUuid) + '/Enable',
    then: function (newValue, devName, cellName) {
      runShellCommand('nmcli connection up ' + connectionName, { captureOutput: false });
    },
  });

  defineRule('whenDisable' + connectionUuid, {
    whenChanged: getVirtualDeviceName(connectionUuid) + '/Disable',
    then: function (newValue, devName, cellName) {
      runShellCommand('nmcli connection down ' + connectionName, { captureOutput: false });
    },
  });

  defineRule('whenStateCnanged' + connectionUuid, {
    whenChanged: getVirtualDeviceName(connectionUuid) + '/State',
    then: function (newValue, devName, cellName) {
      var timerName = 'updateNetwork' + connectionUuid;
      if (newValue == 'activated') {
        startTicker(timerName, 60000);
      } else {
        timers[timerName].stop();
      }
      updateNetworking(connectionName, connectionUuid, connectionDevice);
    },
  });

  defineRule('whenUpdateMoment' + connectionUuid, {
    when: function () {
      return timers['updateNetwork' + connectionUuid].firing;
    },
    then: function () {
      updateNetworking(connectionName, connectionUuid, connectionDevice);
    },
  });
}

function devicesInitialize() {
  runShellCommand('nmcli -f name,uuid,type,device  c s', {
    captureOutput: true,
    exitCallback: function (exitCode, capturedOutput) {
      var connectionsList = capturedOutput.split(/\r?\n/);
      for (var i = 1; i < connectionsList.length - 1; i++) {
        var dataList = connectionsList[i].split(/ +/);
        var name = dataList[0];
        var uuid = dataList[1];
        var type = dataList[2];
        var device = dataList[3];

        defineNewDevice(name, uuid, type);
        defineNewRules(name, uuid, device);
      }
    },
  });
}

function devicesUpdate() {
  runShellCommand('nmcli -f name,uuid,type,device,active,state c s', {
    captureOutput: true,
    exitCallback: function (exitCode, capturedOutput) {
      var connectionsList = capturedOutput.split(/\r?\n/);

      for (var i = 1; i < connectionsList.length - 1; i++) {
        var dataList = connectionsList[i].split(/ +/);
        var name = dataList[0];
        var uuid = dataList[1];
        var type = dataList[2];
        var device = dataList[3];
        var active = dataList[4];
        var state = dataList[5];

        if (getDevice(getVirtualDeviceName(uuid)) == undefined) {
          defineNewDevice(name, uuid, type);
          defineNewRules(name, uuid, device);
        }

        var mqttDevice = getDevice(getVirtualDeviceName(uuid));

        mqttDevice.getControl('Device').setValue(device);
        mqttDevice.getControl('Active').setValue(active == 'yes');
        mqttDevice.getControl('State').setValue(state);
      }
    },
  });
}

devicesInitialize();
setInterval(devicesUpdate, 2000);
