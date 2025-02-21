# nRF Device Test Suite

Automated test suite for nRF devices that performs network connectivity, WiFi scanning, and location service tests.

## Features

- RTT communication verification
- Network connection monitoring
- WiFi scanning capabilities
- Location services testing:
  - WiFi-based location
  - Cellular-based location
  - GNSS location (optional)
- Automated test reporting
- JSON output generation

## Requirements

```bash
pip install -r requirements.txt
```

## Installation
1. Install required Python packages:
```bash
pip3 install -r requirements.txt
```

2. Additional requirements:
- nRF Connect SDK v2.9.0 or later
- J-Link software (latest version)
- nRF9160 development kit

## Usage
Basic usage:
```bash
python3 test_script.py --hex merged.hex
```

Optional arguments:
```bash
--hex       Path to hex file (required)
--sn        J-Link serial number (optional)
--verbose   Enable verbose output (optional)
```

## Configuration
The script supports:
- LTE-M and NB-IoT network modes
- PDN connection setup for data services
- WiFi scanning with signal strength reporting
- Location services using WiFi and cellular
- RTT communication verification
- Boot sequence monitoring

## Status Indicators
- ✓ : Test passed/feature working
- ✗ : Test failed/feature not working

## Output Example
```
=== Starting WiFi Scan ===
Waiting for scan results...
Num  | SSID                             (len) | Chan (Band)   | RSSI | Security
1    | Network1                         8     | 6    (2.4GHz) | -50  | WPA2-PSK
...
WiFi scan: ✓

=== Starting WiFi Location Test ===
Waiting for location results...
Location: 52.3740, 4.8897
WiFi location: ✓
```

## Error Handling
The script includes error handling for:
- Device connection failures
- Network registration issues
- PDN connection problems
- WiFi scan timeouts
- Location service failures

## Troubleshooting
1. If device not found:
   - Check USB connection
   - Verify J-Link driver installation
   - Check device serial number

2. If network registration fails:
   - Verify SIM card installation
   - Check antenna connection
   - Verify network coverage

3. If WiFi scan fails:
   - Check WiFi module initialization
   - Verify antenna connection

## Contributing
Feel free to submit issues and enhancement requests.

## License
This project is licensed under the MIT License - see the LICENSE file for details.