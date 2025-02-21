# nRF Device Test Suite

Automated test suite for nRF devices that performs network connectivity, WiFi scanning, and location service tests.

## Overview
This test suite provides automated testing for nRF9160 devices, including network connectivity verification, WiFi scanning capabilities, and various location services testing (WiFi, Cellular, and GNSS).

## Features
- Device Information Retrieval (IMEI, IMSI)
- RTT Communication Verification
- Network Connection Monitoring
- WiFi Capabilities:
  - Network Scanning
  - WiFi-based Location
- Location Services:
  - WiFi-based Positioning
  - Cellular-based Positioning
  - GNSS Location (Optional, User-prompted)
- Automated Test Reporting:
  - JSON Format Output
  - Detailed Failure Reporting
  - Test Result Summary

## Requirements
- Python 3.7 or later
- nRF Connect SDK v2.9.0 or later
- J-Link Software (Latest Version)
- nRF9160 Development Kit

## Installation

1. Clone the repository:
```bash
git clone https://github.com/semihaydinai/test_script_nRF91.git
cd test_script_nRF91
```

2. Install required packages:
```bash
pip3 install -r requirements.txt
```

## Usage

Basic usage:
```bash
python3 test_script.py
```

## Test Flow
1. **Device Connection**
   - RTT Communication Setup
   - AT Command Verification
   - Device Info Collection (IMEI/IMSI)

2. **Network Tests**
   - Registration Status Check
   - Home/Roaming Network Detection

3. **WiFi Tests**
   - Network Scanning
   - Access Point Detection
   - Signal Strength Measurement

4. **Location Services**
   - WiFi-based Location
   - Cellular-based Location
   - GNSS Location (Optional)

## Output Format
The script generates a JSON report containing:
```json
{
  "imei": "123456789012345",
  "imsi": "234567890123456",
  "timestamp": "2025-02-21T14:30:00.123456",
  "overall_status": "Successful",
  "test_results": {
    "wifi_scan": {
      "status": "Successful",
      "details": "Found 4 networks"
    },
    "wifi_location": {
      "status": "Successful",
      "coordinates": {
        "latitude": 52.3761,
        "longitude": 4.8962
      }
    }
  }
}
```

## Error Handling
- Comprehensive error reporting with actual device responses
- Automatic cleanup on script interruption
- Detailed failure reasons in JSON output

## Contributing
1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License
This project is licensed under the MIT License - see the LICENSE file for details.