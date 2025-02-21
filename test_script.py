import argparse
import time
import re
import os
import signal
import sys
import atexit
import logging
from colorama import Fore, init
from pynrfjprog import HighLevel
from pynrfjprog.APIError import APIError
from pylink import JLink
import json
from datetime import datetime

# Configuration constants
ERASE_WAIT = 5  # seconds to wait after erase
PROGRAM_WAIT = 5  # seconds to wait after programming

global jlink_instance
jlink_instance = None

def cleanup_rtt():
    """Cleanup function to ensure RTT is properly closed"""
    global jlink_instance
    if jlink_instance:
        try:
            print("\nCleaning up RTT connection...")
            jlink_instance.rtt_stop()
            jlink_instance.close()
            print("RTT connection closed successfully")
        except Exception as e:
            print(f"Error during RTT cleanup: {e}")

def signal_handler(signum, frame):
    """Handle interruption signals"""
    print("\nSignal received, performing cleanup...")
    cleanup_rtt()
    sys.exit(0)

# Register the cleanup function and signal handlers
atexit.register(cleanup_rtt)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def cleanup_old_reports():
    """Remove old JSON test reports"""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        deleted_count = 0
        for file in os.listdir(current_dir):
            if file.startswith("test_report_") and file.endswith(".json"):
                file_path = os.path.join(current_dir, file)
                os.remove(file_path)
                deleted_count += 1
        if deleted_count > 0:
            print(f"Cleaned up {deleted_count} old test report(s)")
    except Exception as e:
        print(f"Warning: Could not clean up old reports: {e}")

def get_serial_number(api, device_index=0):
    try:
        serial_numbers = api.get_connected_probes()
        print(f"Found devices: {serial_numbers}")
        if not serial_numbers:
            raise Exception("No nRF devices found.")
        if device_index >= len(serial_numbers):
            raise Exception(f"Device index {device_index} is out of range. Found {len(serial_numbers)} device(s).")
        return serial_numbers[device_index]
    except APIError as e:
        raise Exception(f"Error getting serial number: {e}")

#def flash_custom_modem_shell(api, hex_file_path, serial_number, retries=3,
#                           interactive=True, erase_wait=ERASE_WAIT, 
 #                          program_wait=PROGRAM_WAIT):
  #  """Flash Custom Modem Shell without resetting device"""
    for attempt in range(retries):
        try:
            print(f"\nAttempting to flash Custom Modem Shell (Attempt {attempt + 1}/{retries})...")
            with HighLevel.DebugProbe(api, serial_number) as probe:
                # Erase without reset
                print("\nErasing device: ", end='', flush=True)
                probe.erase()
                for i in range(10):
                    print("▓", end='', flush=True)
                    time.sleep(erase_wait/10)
                print(" Done")
                
                # Program options - removed reset actions
                program_options = HighLevel.ProgramOptions(
                    verify=HighLevel.VerifyAction.VERIFY_READ,
                    erase_action=HighLevel.EraseAction.ERASE_NONE,  # Changed from ERASE_ALL
                    qspi_erase_action=HighLevel.EraseAction.ERASE_NONE,
                    reset=HighLevel.ResetAction.RESET_NONE  # Changed from RESET_SYSTEM
                )
                
                # Programming with progress bar
                print("Programming: ", end='', flush=True)
                probe.program(hex_file_path, program_options)
                for i in range(20):
                    print("▓", end='', flush=True)
                    time.sleep(program_wait/20)
                print(" Done")
                
                # Removed verification section that included reset
                
            print("\nCustom Modem Shell successfully installed.")
            return True
            
        except APIError as e:
            print(f"\nError: {e}")
            if attempt < retries - 1:
                if interactive:
                    retry = input("Do you want to retry flashing? (y/n): ").lower()
                    if retry != 'y':
                        return False
                else:
                    print(f"\nAuto-retrying... ({attempt + 2}/{retries})")
                    time.sleep(1)
            else:
                print("\nMax retries reached. Flashing failed.")
                return False

def setup_rtt(serial_number, device_family="nRF9160_xxAA", connection_timeout=5, rtt_timeout=90):
    """Setup RTT communication without resetting device"""
    global jlink_instance
    try:
        print("Initializing J-Link connection...")
        jlink_instance = JLink()
        jlink_instance.open(serial_no=serial_number)
        
        if not jlink_instance.connected():
            raise Exception("J-Link connection failed")
        
        # Configure interface and speed but don't reset
        print("Setting JTAG interface and initial speed...")
        jlink_instance.set_tif(1)  # JTAG
        jlink_instance.set_speed(1000)
        
        # Connect without reset
        print(f"Connecting to {device_family}...")
        jlink_instance.connect(device_family, speed='auto', verbose=True)
        
        # Configure RTT without stopping
        print("Configuring RTT parameters...")
        jlink_instance.rtt_start(False)  # Don't reset target
        
        # Wait for RTT Control Block
        print("Waiting for RTT Control Block...")
        start_time = time.time()
        while time.time() - start_time < rtt_timeout:
            try:
                if jlink_instance.rtt_get_num_up_buffers() > 0:
                    print("\nRTT Control Block found!")
                    return jlink_instance
            except Exception:
                time.sleep(0.5)
                
        raise Exception("RTT Control Block not found within timeout")
        
    except Exception as e:
        if jlink_instance:
            jlink_instance.close()
            jlink_instance = None
        raise Exception(f"RTT setup failed: {str(e)}")

def send_command(jlink, command, timeout=10, buffer_size=1024):
    """Send command and read response with improved AT handling"""
    try:
        # Clear buffer
        data = jlink.rtt_read(0, buffer_size)
        while data:  # Clear any pending data
            data = jlink.rtt_read(0, buffer_size)
            time.sleep(0.1)
        
        # Send command
        jlink.rtt_write(0, command.encode('utf-8'))
        
        # Read response with timeout
        response = ""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            data = jlink.rtt_read(0, buffer_size)
            if data:
                # Handle both bytes and list responses
                if isinstance(data, (bytes, bytearray)):
                    text = data.decode('utf-8', errors='ignore')
                elif isinstance(data, list):
                    text = ''.join([chr(x) for x in data])
                else:
                    continue
                
                response += text
                
                # Check for common completion indicators
                if any(x in response for x in ['OK\r\n', 'ERROR\r\n', 'mosh:~$']):
                    # Give a small delay to catch any trailing data
                    time.sleep(0.2)
                    final_data = jlink.rtt_read(0, buffer_size)
                    if final_data:
                        if isinstance(final_data, (bytes, bytearray)):
                            response += final_data.decode('utf-8', errors='ignore')
                        elif isinstance(final_data, list):
                            response += ''.join([chr(x) for x in final_data])
                    break
            
            time.sleep(0.1)
        
        return response.strip()
        
    except Exception as e:
        print(f"Command error: {str(e)}")
        return ""

def verify_rtt_communication(jlink):
    """Verify RTT communication is working"""
    print("\nVerifying RTT Communication")
    print("===========================\n")
    
    try:
        # Clear buffer more thoroughly
        for _ in range(3):
            jlink.rtt_read(0, 1024)
            time.sleep(0.5)
        
        # Exit any existing AT command mode and wait
        send_command(jlink, "\x18\x11\r\n", timeout=2)  # Send Ctrl-X Ctrl-Q
        time.sleep(2)
        
        # Clear buffer again
        jlink.rtt_read(0, 1024)
        
        # Enter AT command mode as a single command
        print("Sending: at at_cmd_mode start")
        response = send_command(jlink, "at at_cmd_mode start\r\n", timeout=5)
        print(f"Response: {response}")
        
        if 'MoSh AT command mode started' in response:
            print("AT command mode: ✓")
            time.sleep(1)
            
            # Verify with basic AT command
            print("\nSending: AT")
            response = send_command(jlink, "AT\r\n", timeout=5)
            print(f"Response: {response}")
            
            if 'OK' in response:
                print("Basic AT test: ✓")
                print("\nRTT communication verified successfully")
                return True
                
        print("AT command mode: ✗")
        return False
            
    except Exception as e:
        print(f"\nRTT verification failed: {e}")
        return False

def monitor_network_connection(jlink, check_interval=5, timeout=300):
    """Monitor network connection status with command visibility"""
    print("\n=== Network Connection Monitor ===")
    
    start_time = time.time()
    last_check = 0
    last_status = None
    
    network_status = {
        'connected': False,
        'details': 'Not connected'
    }
    
    while time.time() - start_time < timeout:
        current_time = time.time()
        
        if current_time - last_check >= check_interval:
            # Show the command being sent
            print("\nSending: AT+CEREG?")
            response = send_command(jlink, "AT+CEREG?\r\n", timeout=2)
            print(f"Response: {response}")
            last_check = current_time
            status = None  # Initialize status variable
            
            # Interpret the response
            if "+CEREG:" in response:
                if "+CEREG: 5,1" in response:
                    network_status['connected'] = True
                    network_status['details'] = "Connected (Home)"
                    return network_status
                elif "+CEREG: 5,5" in response:
                    network_status['connected'] = True
                    network_status['details'] = "Connected (Roaming)"
                    return network_status
                elif "+CEREG: 5,2" in response:
                    status = "Searching..."
                    network_status['details'] = status
                elif "+CEREG: 5,0" in response:
                    status = "Not registered ✗"
                    network_status['details'] = status
                elif "+CEREG: 5,3" in response:
                    status = "Registration denied ✗"
                    network_status['details'] = status
                else:
                    status = "Unknown status ?"
                    network_status['details'] = status
                
                if status != last_status:
                    print(f"Interpreted: Network Status: {status}")
                    last_status = status
            
            print(".", end='', flush=True)
        
        time.sleep(1)
    
    network_status['details'] = "Connection timeout"
    return network_status

def perform_wifi_scan(jlink, timeout=12):
    """Perform WiFi scan and return results"""
    print("\n=== Starting WiFi Scan ===")
    
    # Make sure we're out of AT command mode first
    send_command(jlink, "\x18\x11", timeout=1)  # Exit AT mode
    time.sleep(2)  # Give time for mode switch
    
    # Clear any pending data
    jlink.rtt_read(0, 1024)
    
    # Send scan command
    send_command(jlink, "wifi scan\r\n", timeout=5)
    print("Waiting for scan results...")
    
    # Wait for scan results
    start_time = time.time()
    scan_output = ""
    scan_complete = False
    
    networks_found = 0
    while time.time() - start_time < timeout:
        data = jlink.rtt_read(0, 1024)
        if data:
            if isinstance(data, (bytes, bytearray)):
                text = data.decode('utf-8', errors='ignore')
            elif isinstance(data, list):
                text = ''.join([chr(x) for x in data])
            else:
                continue
                
            # Store and print all non-empty, non-prompt output
            if text.strip() and not text.strip() == "mosh:~$":
                print(text.strip())
                scan_output += text
                
                # Check for scan completion
                if "Scan request done" in text:
                    # If we have both table data and completion message
                    if "|" in scan_output and "SSID" in scan_output:
                        networks = len([line for line in scan_output.split('\n') 
                                     if "|" in line and "SSID" not in line])
                        print(f"\nWiFi scan completed successfully - {networks} networks found")
                        print("WiFi scan: ✓")
                        return networks  # Return number of networks instead of True
                    scan_complete = True
                    break
                
        time.sleep(0.1)
    
    if not scan_complete:
        print("\nWiFi scan timeout")
    print("WiFi scan: ✗")
    return 0  # Return 0 if scan failed

def perform_wifi_location(jlink, timeout=25):
    """Perform WiFi location test with PDN verification"""
    print("\n=== Starting WiFi Location Test ===")
    
    # Clear any pending data
    jlink.rtt_read(0, 1024)
    time.sleep(1)
    
    # Send location command with WiFi method
    command = "location get --method wifi --wifi_timeout 60000\r\n"
    send_command(jlink, command, timeout=5)
    print("Waiting for location results...")
    
    # Wait for location results
    start_time = time.time()
    location_output = ""
    location_found = False
    
    while time.time() - start_time < timeout:
        data = jlink.rtt_read(0, 1024)
        if data:
            if isinstance(data, (bytes, bytearray)):
                text = data.decode('utf-8', errors='ignore')
            elif isinstance(data, list):
                text = ''.join([chr(x) for x in data])
            else:
                continue
                
            # Store and print all non-empty, non-prompt output
            if text.strip() and not text.strip() == "mosh:~$":
                print(text.strip())
                location_output += text
                
                if "Location:" in text or "latitude:" in text:
                    location_found = True
                if "Location request completed" in text and location_found:
                    print("\nWiFi location test completed successfully")
                    print("WiFi location: ✓")
                    return True, location_output
                elif "PDN context is NOT active" in text:
                    print("\nPDN connection lost during location request")
                    return False, location_output
                
        time.sleep(0.1)
    
    print("\nWiFi location timeout - no results received")
    print("WiFi location: ✗")
    return False, location_output

def perform_cellular_location(jlink, timeout=25):
    """Perform cellular location test"""
    print("\n=== Starting Cellular Location Test ===")
    
    # Clear any pending data
    jlink.rtt_read(0, 1024)
    time.sleep(1)
    
    # Send cellular location command
    command = "location get --method cellular --cellular_service nrf\r\n"
    send_command(jlink, command, timeout=5)
    print("Waiting for cellular location results...")
    
    # Wait for location results
    start_time = time.time()
    location_output = ""
    location_found = False
    
    while time.time() - start_time < timeout:
        data = jlink.rtt_read(0, 1024)
        if data:
            if isinstance(data, (bytes, bytearray)):
                text = data.decode('utf-8', errors='ignore')
            elif isinstance(data, list):
                text = ''.join([chr(x) for x in data])
            else:
                continue
                
            # Store and print all non-empty, non-prompt output
            if text.strip() and not text.strip() == "mosh:~$":
                print(text.strip())
                location_output += text
                
                if "Location:" in text or "latitude:" in text:
                    location_found = True
                if "Location request completed" in text and location_found:
                    print("\nCellular location test completed successfully")
                    print("Cellular location: ✓")
                    return True, location_output
                elif "PDN context is NOT active" in text:
                    print("\nPDN connection lost during cellular location request")
                    return False, location_output
                
        time.sleep(0.1)
    
    print("\nCellular location timeout - no results received")
    print("Cellular location: ✗")
    return False, location_output

def perform_gnss_location(jlink, timeout=40):
    """Perform GNSS location test"""
    print("\n=== Starting GNSS Location Test ===")
    
    # Clear any pending data
    jlink.rtt_read(0, 1024)
    time.sleep(1)
    
    # Send GNSS location command
    command = "location get --method gnss --gnss_timeout 40000\r\n"
    send_command(jlink, command, timeout=5)
    print("Waiting for GNSS location results...")
    
    # Wait for location results
    start_time = time.time()
    location_output = ""
    location_found = False
    
    while time.time() - start_time < timeout:
        data = jlink.rtt_read(0, 1024)
        if data:
            if isinstance(data, (bytes, bytearray)):
                text = data.decode('utf-8', errors='ignore')
            elif isinstance(data, list):
                text = ''.join([chr(x) for x in data])
            else:
                continue
                
            # Store and print all non-empty, non-prompt output
            if text.strip() and not text.strip() == "mosh:~$":
                print(text.strip())
                location_output += text
                
                if "Location:" in text or "latitude:" in text:
                    location_found = True
                if "Location request completed" in text and location_found:
                    print("\nGNSS location test completed successfully")
                    print("GNSS location: ✓")
                    return True, location_output
                
        time.sleep(0.1)
    
    print("\nGNSS location timeout - no results received")
    print("GNSS location: ✗")
    return False, location_output

def get_device_info(jlink):
    """Get IMEI and IMSI numbers from device"""
    print("\nGetting device information...")
    
    # Get IMEI
    response = send_command(jlink, "AT+CGSN\r\n", timeout=5)
    imei = response.split('\r\n')[1] if '\r\n' in response else 'Unknown'
    
    # Get IMSI
    response = send_command(jlink, "AT+CIMI\r\n", timeout=5)
    imsi = response.split('\r\n')[1] if '\r\n' in response else 'Unknown'
    
    return imei, imsi

def extract_coordinates(location_text):
    """Extract latitude and longitude from location response"""
    coords = {'latitude': 'Unknown', 'longitude': 'Unknown'}
    
    if "latitude:" in location_text and "longitude:" in location_text:
        try:
            lat_match = re.search(r'latitude:\s*([-\d.]+)', location_text)
            lon_match = re.search(r'longitude:\s*([-\d.]+)', location_text)
            if lat_match and lon_match:
                coords['latitude'] = float(lat_match.group(1))
                coords['longitude'] = float(lon_match.group(1))
        except:
            pass
    return coords

def generate_test_report(test_results, failure_reasons, imei, imsi, network_status):
    """Generate JSON report from test results"""
    
    # Determine overall status
    all_passed = all(result is True or result == 'skipped' for result in test_results.values())
    gnss_skipped = test_results.get('gnss_location') == 'skipped'
    
    # Set main status
    if all_passed and gnss_skipped:
        status = "Successful + GNSS skipped"
    elif all_passed:
        status = "Successful"
    elif gnss_skipped:
        status = "Failed + GNSS skipped"
    else:
        status = "Failed"
    
    # Create test details
    test_details = {
        "wifi_scan": {
            "status": "Successful" if test_results.get('wifi_scan', 0) > 0 else "Failed",
            "details": f"Found {test_results.get('wifi_scan', 0)} networks"
        },
        "network_status": {
            "status": "Successful" if network_status['connected'] else "Failed",
            "details": network_status['details']
        },
        "wifi_location": {
            "status": "Successful" if test_results.get('wifi_location') else "Failed",
            "coordinates": {
                "latitude": "Unknown",
                "longitude": "Unknown"
            } if not test_results.get('wifi_location') else test_results.get('wifi_coordinates')
        },
        "cellular_location": {
            "status": "Successful" if test_results.get('cellular_location') else "Failed",
            "coordinates": {
                "latitude": "Unknown",
                "longitude": "Unknown"
            } if not test_results.get('cellular_location') else test_results.get('cellular_coordinates')
        },
        "gnss_location": {
            "status": "Skipped" if test_results.get('gnss_location') == 'skipped' else 
                     "Successful" if test_results.get('gnss_location') else "Failed",
            "coordinates": {
                "latitude": "Unknown",
                "longitude": "Unknown"
            } if test_results.get('gnss_location') not in [True, 'skipped'] else 
            "Skipped by user" if test_results.get('gnss_location') == 'skipped' else 
            test_results.get('gnss_coordinates')
        }
    }
    
    # Create final report
    report = {
        "imei": imei,
        "imsi": imsi,
        "timestamp": datetime.now().isoformat(),
        "overall_status": status,
        "test_results": test_details
    }
    
    return report

def main():
    """Main function with all tests including GNSS"""    # Clean up old reports first
    cleanup_old_reports()
    
    api = HighLevel.API()
    jlink = None
    test_results = {}
    failure_reasons = {}
    
    try:
        # Setup device communication
        api.open()
        serial_number = get_serial_number(api)
        print(f"Using serial number: {serial_number}\n")
        
        # Critical tests - These must pass to continue
        jlink = setup_rtt(serial_number)
        if not jlink:
            print("RTT setup: ✗")
            return
            
        if not verify_rtt_communication(jlink):
            print("RTT verification: ✗")
            return
        
        # Get device information after RTT verification
        imei, imsi = get_device_info(jlink)
        print(f"Device IMEI: {imei}")
        print(f"Device IMSI: {imsi}")
        
        # Monitor network connection
        network_status = monitor_network_connection(jlink)
        if not network_status['connected']:
            print(f"Network connection: ✗ ({network_status['details']})")
            return
            
        # Non-critical tests - Continue regardless of result
        print("\nStarting WiFi scan test...")
        wifi_networks = perform_wifi_scan(jlink)
        test_results['wifi_scan'] = wifi_networks
        if not test_results['wifi_scan']:
            failure_reasons['wifi_scan'] = "No networks found or scan timeout"
            print(f"WiFi scan failed: ✗ (Reason: {failure_reasons['wifi_scan']})")
        
        # Initialize location outputs
        wifi_location_output = ""
        cellular_location_output = ""
        gnss_location_output = ""
        
        # Update location test calls to store their outputs
        print("\nStarting WiFi location test...")
        test_results['wifi_location'], wifi_location_output = perform_wifi_location(jlink)
        
        print("\nStarting Cellular location test...")
        test_results['cellular_location'], cellular_location_output = perform_cellular_location(jlink)
        
        # Store coordinates in test results
        if test_results.get('wifi_location'):
            test_results['wifi_coordinates'] = extract_coordinates(wifi_location_output)
            
        if test_results.get('cellular_location'):
            test_results['cellular_coordinates'] = extract_coordinates(cellular_location_output)
            
        if test_results.get('gnss_location') is True:
            test_results['gnss_coordinates'] = extract_coordinates(gnss_location_output)
        
        # GNSS test with user prompt
        gnss_response = input("\nAre you outside? (y/n): ").lower().strip()
        if gnss_response == 'y':
            print("\nStarting GNSS location test...")
            test_results['gnss_location'], gnss_location_output = perform_gnss_location(jlink)
            if not test_results['gnss_location']:
                failure_reasons['gnss_location'] = "No GNSS fix obtained within timeout"
                print(f"GNSS location failed: ✗ (Reason: {failure_reasons['gnss_location']})")
        else:
            print("\nGNSS location test skipped ⏭")
            test_results['gnss_location'] = 'skipped'
        
        # Print comprehensive test results summary
        print("\n=== Test Results Summary ===")
        print("RTT Communication: ✓")
        print("Network Connection: ✓")
        for test in ['wifi_scan', 'wifi_location', 'cellular_location', 'gnss_location']:
            result = test_results.get(test)
            if result is True:
                print(f"{test.replace('_', ' ').title()}: ✓")
            elif result == 'skipped':
                print(f"{test.replace('_', ' ').title()}: ⏭ (Skipped by user)")
            else:
                print(f"{test.replace('_', ' ').title()}: ✗")
                print(f"  Failure Reason: {failure_reasons.get(test, 'Unknown error')}")
        
        if all(result is True or result == 'skipped' for result in test_results.values()):
            print("\nAll tests completed successfully ✓")
        else:
            print("\nSome tests failed, check summary above ⚠")
            print("\nFailure Details:")
            for test, reason in failure_reasons.items():
                print(f"- {test.replace('_', ' ').title()}: {reason}")
        
        # Generate JSON report with device info
        test_results['wifi_networks'] = test_results.get('wifi_scan', 0)
        report = generate_test_report(test_results, failure_reasons, imei, imsi, network_status)
        
        # Save to file
        report_file = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\nTest report saved to: {report_file}")
        
        # Print JSON to console
        print("\nJSON Report:")
        print(json.dumps(report, indent=2))
        
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        if jlink:
            try:
                jlink.close()
            except Exception as e:
                print(f"Cleanup error: {e}")
        api.close()

if __name__ == '__main__':
    main()