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

# Configuration constants
ERASE_WAIT = 5  # seconds to wait after erase
PROGRAM_WAIT = 5  # seconds to wait after programming

init(autoreset=True)

logging.basicConfig(
    level=logging.WARNING,  # Change from INFO to WARNING to reduce output
    format='%(levelname)s: %(message)s',  # Simplified format
    handlers=[
        logging.FileHandler('rtt_debug.log'),  # Full logs still go to file
        logging.StreamHandler()
    ]
)

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

def flash_custom_modem_shell(api, hex_file_path, serial_number, retries=3,
                           interactive=True, erase_wait=ERASE_WAIT, 
                           program_wait=PROGRAM_WAIT):
    """Flash Custom Modem Shell with progress bar"""
    for attempt in range(retries):
        try:
            print(f"\nAttempting to flash Custom Modem Shell (Attempt {attempt + 1}/{retries})...")
            with HighLevel.DebugProbe(api, serial_number) as probe:
                # Erase
                print("\nErasing device: ", end='', flush=True)
                probe.erase()
                for i in range(10):
                    print("▓", end='', flush=True)
                    time.sleep(erase_wait/10)
                print(" Done")
                
                # Program options
                program_options = HighLevel.ProgramOptions(
                    verify=HighLevel.VerifyAction.VERIFY_READ,
                    erase_action=HighLevel.EraseAction.ERASE_ALL,
                    qspi_erase_action=HighLevel.EraseAction.ERASE_NONE,
                    reset=HighLevel.ResetAction.RESET_SYSTEM
                )
                
                # Programming with progress bar
                print("Programming: ", end='', flush=True)
                probe.program(hex_file_path, program_options)
                for i in range(20):
                    print("▓", end='', flush=True)
                    time.sleep(program_wait/20)
                print(" Done")
                
                # Verification
                print("Verifying:  ", end='', flush=True)
                probe.reset()
                for i in range(10):
                    print("▓", end='', flush=True)
                    time.sleep(1/10)
                print(" Done")
                
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
    """Setup RTT communication with improved initialization"""
    global jlink_instance
    try:
        print("Initializing J-Link connection...")
        jlink_instance = JLink()
        jlink_instance.open(serial_no=serial_number)
        
        if not jlink_instance.connected():
            raise Exception("J-Link connection failed")
        
        # Configure interface and speed
        print("Setting JTAG interface and initial speed...")
        jlink_instance.set_tif(1)  # JTAG
        jlink_instance.set_speed(1000)
        
        # Connect and verify
        print(f"Connecting to {device_family}...")
        jlink_instance.connect(device_family, speed='auto', verbose=True)
        
        # Initial reset sequence
        print("Performing reset sequence...")
        jlink_instance.reset(halt=True)
        time.sleep(0.5)
        jlink_instance.reset(halt=False)
        time.sleep(2)  # Give more time after reset
        
        # Configure RTT
        print("Configuring RTT parameters...")
        jlink_instance.rtt_stop()  # Stop any existing RTT
        time.sleep(1)
        jlink_instance.rtt_start(False)
        
        # Wait for RTT Control Block
        print("Waiting for RTT Control Block...")
        start_time = time.time()
        while time.time() - start_time < rtt_timeout:
            try:
                if jlink_instance.rtt_get_num_up_buffers() > 0:
                    print("\nRTT Control Block found!")
                    time.sleep(3)  # Additional wait after finding RTT block
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
    """Send command and read response with proper data handling"""
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
                    response += data.decode('utf-8', errors='ignore')
                elif isinstance(data, list):
                    response += ''.join([chr(x) for x in data])
                
                if any(x in response for x in ['OK\r\n', 'ERROR\r\n', 'mosh:~$']):
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
        # Clear any pending data
        time.sleep(2)
        jlink.rtt_read(0, 1024)
        
        print("Verifying RTT communication...")
        
        # Enter AT command mode
        response = send_command(jlink, "at at_cmd_mode start\r\n", timeout=5)
        print(f"AT mode response: {response}")
        
        if 'MoSh AT command mode started' in response:
            print("\nAT command mode started successfully")
            
            # Basic AT test
            response = send_command(jlink, "AT\r\n", timeout=5)
            print(f"AT command response:\n{response}")
            
            if 'OK' in response:
                print("\nRTT communication verified successfully")
                return True
            else:
                print("\nFailed AT command test")
                return False
        else:
            print("\nFailed to start AT command mode")
            return False
            
    except Exception as e:
        print(f"\nRTT verification failed: {e}")
        return False

def wait_for_device_stable(jlink, timeout=60):
    """Wait for device to become stable with improved detection"""
    print("Waiting for device to stabilize...")
    start_time = time.time()
    boot_seen = False
    prompt_seen = False
    
    # First, exit AT command mode
    send_command(jlink, "\x18\x11", timeout=2)  # Send Ctrl-X Ctrl-Q
    time.sleep(2)
    
    # Clear any pending data
    jlink.rtt_read(0, 1024)
    
    while time.time() - start_time < timeout:
        try:
            response = send_command(jlink, "\r\n", timeout=2)
            
            if response:
                # Only print non-error responses for cleaner output
                if 'ERROR' not in response:
                    print(f"Response: {response}")
            
            # Check for boot sequence
            if '*** Booting nRF Connect SDK' in response:
                boot_seen = True
                print("Boot sequence detected")
            
            # Check for stable prompt
            if 'mosh:~$' in response and not 'ERROR' in response:
                prompt_seen = True
                print("Command prompt detected")
            
            # Check for stability conditions
            if prompt_seen and not 'ERROR' in response:
                print("Device stable")
                time.sleep(2)  # Give additional time after stability
                return True
                
            time.sleep(0.5)  # Reduced polling interval
            
        except Exception as e:
            print(f"Stability check error: {e}")
            time.sleep(1)
            
    print("Device stabilization timeout")
    return False

def monitor_boot_sequence(jlink, timeout=60):
    """Monitor device boot sequence and show terminal output"""
    print("\nMonitoring boot sequence...")
    start_time = time.time()
    boot_complete = False
    boot_log = ""
    boot_markers = {
        'sdk_version': False,  # "*** Booting nRF Connect SDK"
        'reset_reason': False, # "Reset reason:"
        'mosh_version': False, # "MOSH version:"
        'wifi_init': False,    # "wifi_nrf:"
        'modem_event': False,  # "Modem domain event:"
        'network_status': False # "Network registration status:"
    }
    
    try:
        # Reset the device
        print("Resetting device...")
        jlink.reset(halt=True)
        time.sleep(0.5)
        jlink.reset(halt=False)
        
        # Clear any pending data
        jlink.rtt_read(0, 1024)
        
        # Monitor boot sequence
        while time.time() - start_time < timeout:
            data = jlink.rtt_read(0, 1024)
            if data:
                if isinstance(data, (bytes, bytearray)):
                    text = data.decode('utf-8', errors='ignore')
                elif isinstance(data, list):
                    text = ''.join([chr(x) for x in data])
                else:
                    continue
                    
                boot_log += text
                print(text, end='')  # Print in real-time
                
                # Check for boot markers
                if "*** Booting nRF Connect SDK" in text:
                    boot_markers['sdk_version'] = True
                if "Reset reason:" in text:
                    boot_markers['reset_reason'] = True
                if "MOSH version:" in text:
                    boot_markers['mosh_version'] = True
                if "wifi_nrf:" in text:
                    boot_markers['wifi_init'] = True
                if "Modem domain event:" in text:
                    boot_markers['modem_event'] = True
                if "Network registration status:" in text:
                    boot_markers['network_status'] = True
                
                # Check if all required markers are found
                if all([
                    boot_markers['sdk_version'],
                    boot_markers['reset_reason'],
                    boot_markers['mosh_version'],
                    boot_markers['wifi_init']
                ]):
                    boot_complete = True
                    time.sleep(2)  # Wait for any remaining output
                    break
                    
            time.sleep(0.1)
            
        if not boot_complete:
            print("\nBoot sequence timeout")
            return False
            
        print("\nBoot sequence completed with markers:")
        for marker, status in boot_markers.items():
            print(f"- {marker}: {'✓' if status else '✗'}")
            
        return True
        
    except Exception as e:
        print(f"\nBoot sequence monitoring failed: {e}")
        return False

def check_network_status(jlink):
    """Check device network status through RTT terminal"""
    print("\n=== Checking Network Status ===")
    
    # Enter AT command mode
    send_command(jlink, "at at_cmd_mode start\r\n", timeout=2)
    time.sleep(1)
    
    # Network status commands
    commands = [
        ("AT+CIMI", "IMSI number"),
        ("AT+CGSN=1", "IMEI number"),
        ("AT+CEREG?", "Network registration status"),
        ("AT%XSYSTEMMODE?", "System mode"),
        ("AT+CESQ", "Signal quality"),
        ("AT+COPS?", "Current operator")
    ]
    
    network_info = {}
    for cmd, desc in commands:
        print(f"\n{desc}:")
        response = send_command(jlink, f"{cmd}\r\n", timeout=5)
        print(response)
        network_info[desc] = response
    
    # Exit AT command mode
    send_command(jlink, "\x18\x11", timeout=1)
    return network_info

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
                        return True
                    scan_complete = True
                    break
                
        time.sleep(0.1)
    
    if not scan_complete:
        print("\nWiFi scan timeout")
    print("WiFi scan: ✗")
    return False

def perform_wifi_location(jlink, timeout=25):
    """Perform WiFi location test with PDN verification"""
    print("\n=== Starting WiFi Location Test ===")
    
    # Verify PDN connection is active
    if not verify_pdn_status(jlink):
        print("PDN connection not active, attempting to reconnect...")
        if not setup_pdn_connection(jlink):
            print("Cannot proceed with location test - PDN connection failed")
            return False
    
    # Clear any pending data
    jlink.rtt_read(0, 1024)
    time.sleep(1)
    
    # Send location command with both WiFi and cellular methods
    command = "location get --method wifi --wifi_timeout 60000 --method cellular --cellular_service nrf\r\n"
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
                    return True
                elif "PDN context is NOT active" in text:
                    print("\nPDN connection lost during location request")
                    return False
                
        time.sleep(0.1)
    
    print("\nWiFi location timeout - no results received")
    print("WiFi location: ✗")
    return False

def setup_network_mode(jlink):
    """Setup network mode for both LTE-M and NB-IoT"""
    print("\n=== Setting up Network Mode ===")
    
    send_command(jlink, "at at_cmd_mode start\r\n", timeout=2)
    time.sleep(1)
    
    # Setup for both LTE-M and NB-IoT
    commands = [
        ("AT+CFUN=0", "Disable radio"),
        ("AT%XSYSTEMMODE=1,1,0,0", "Set LTE-M and NB-IoT mode"),  # Changed to enable both
        ("AT+CEREG=5", "Enable network registration URC"),
        ("AT+CFUN=1", "Enable radio")
    ]
    
    for cmd, desc in commands:
        print(f"\n{desc}:")
        response = send_command(jlink, f"{cmd}\r\n", timeout=5)
        print(response)
        time.sleep(2)
    
    send_command(jlink, "\x18\x11", timeout=1)

def setup_pdn_connection(jlink, timeout=30):
    """Setup PDN connection for location services"""
    print("\n=== Setting up PDN Connection ===")
    
    # Enter AT command mode
    send_command(jlink, "at at_cmd_mode start\r\n", timeout=2)
    time.sleep(1)
    
    # PDN setup commands in correct order
    commands = [
        ("AT+CFUN=0", "Disable radio for PDN setup"),
        ("AT+CGEREP=1", "Enable packet domain event reporting"),
        ("AT+CNEC=16", "Enable network error code reporting"),
        ("AT%XNEWCID?", "Query available CIDs"),
        ("AT+CGDCONT=0,\"IP\",\"internet\"", "Configure default PDP context"),
        ("AT+CFUN=1", "Enable radio"),
        # Wait for network registration before activating PDN
        ("AT+CEREG?", "Check network registration"),
        ("AT+CGACT=0,0", "Deactivate any existing PDN contexts"),
        ("AT+CGACT=1,0", "Activate PDN context"),
        ("AT+CGACT?", "Verify PDN context"),
        ("AT%XGETPDNID=0", "Get PDN ID for context")
    ]
    
    pdn_active = False
    pdn_id = None
    registration_status = False
    
    for cmd, desc in commands:
        print(f"\n{desc}:")
        response = send_command(jlink, f"{cmd}\r\n", timeout=5)
        print(response)
        
        # Check registration status before activating PDN
        if cmd == "AT+CEREG?":
            if any(status in response for status in ["+CEREG: 5,1", "+CEREG: 5,5"]):
                registration_status = True
                print("Network registered, proceeding with PDN activation")
            else:
                print("Waiting for network registration...")
                # Add registration wait loop here
                start_time = time.time()
                while time.time() - start_time < 30:  # 30 second timeout
                    response = send_command(jlink, "AT+CEREG?\r\n", timeout=2)
                    if any(status in response for status in ["+CEREG: 5,1", "+CEREG: 5,5"]):
                        registration_status = True
                        print("Network registered successfully")
                        break
                    time.sleep(2)
        
        # Only proceed with PDN activation if registered
        if "AT+CGACT=1,0" in cmd and not registration_status:
            print("Cannot activate PDN - device not registered")
            continue
            
        if "CGACT: 1" in response:
            pdn_active = True
        elif "%XGETPDNID:" in response:
            try:
                pdn_id = response.split(":")[1].strip().split()[0]  # Get first value only
                print(f"PDN ID: {pdn_id}")
            except:
                pass
                
        time.sleep(2)
    
    # Exit AT command mode
    send_command(jlink, "\x18\x11", timeout=1)
    
    if pdn_active and pdn_id:
        print(f"PDN connection established successfully (ID: {pdn_id})")
        return True
    else:
        print("Failed to establish PDN connection")
        if not registration_status:
            print("- Network not registered")
        if not pdn_active:
            print("- PDN context not active")
        if not pdn_id:
            print("- Could not get PDN ID")
        return False

def verify_pdn_status(jlink):
    """Verify PDN connection is active"""
    print("\nVerifying PDN connection status...")
    
    send_command(jlink, "at at_cmd_mode start\r\n", timeout=2)
    time.sleep(1)
    
    # Check PDN status
    response = send_command(jlink, "AT+CGACT?\r\n", timeout=5)
    send_command(jlink, "\x18\x11", timeout=1)
    
    if "CGACT: 1" in response:
        print("PDN connection is active")
        return True
    else:
        print("PDN connection is not active")
        return False

def main():
    """Main function with optimized network checks"""
    api = HighLevel.API()
    jlink = None
    
    try:
        # Initialize and flash
        parser = argparse.ArgumentParser(description="nRF9160 Device Test Script")
        parser.add_argument("--hex", default="merged.hex", help="Path to hex file")
        args = parser.parse_args()
        
        hex_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.hex)
        
        # Setup device communication
        api.open()
        serial_number = get_serial_number(api)
        print(f"Using serial number: {serial_number}\n")
        
        # Flash firmware
        if not flash_custom_modem_shell(api, hex_path, serial_number):
            return
            
        # Setup RTT
        jlink = setup_rtt(serial_number)
        if not jlink or not verify_rtt_communication(jlink):
            return
            
        # Monitor boot sequence
        if not monitor_boot_sequence(jlink):
            print(Fore.RED + "Boot sequence failed")
            return
            
        print(Fore.GREEN + "Boot sequence completed successfully")
        
        # Setup network mode once
        setup_network_mode(jlink)
        
        # Single initial network check
        print("\nChecking initial network status...")
        network_status = check_network_status(jlink)
        
        # Setup PDN connection
        print("\nSetting up PDN connection...")
        if not setup_pdn_connection(jlink):
            print(Fore.RED + "PDN setup failed")
            return
        
        # Perform WiFi scan without redundant checks
        wifi_scan_success = perform_wifi_scan(jlink)
        if not wifi_scan_success:
            print(Fore.RED + "WiFi scan failed")
            return
        
        print(Fore.GREEN + "WiFi scan completed successfully")
        
        # Perform WiFi location test
        wifi_location_success = perform_wifi_location(jlink)
        if not wifi_location_success:
            print(Fore.RED + "WiFi location failed")
            return
            
        print(Fore.GREEN + "WiFi location completed successfully")
        
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        if jlink:
            try:
                jlink.rtt_stop()
                jlink.close()
            except Exception as e:
                print(f"Cleanup error: {e}")
        api.close()

if __name__ == '__main__':
    main()