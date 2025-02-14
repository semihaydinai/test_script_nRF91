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
    level=logging.INFO,  # Changed from DEBUG to INFO
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('rtt_debug.log'),
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
    for attempt in range(retries):
        try:
            print(f"Attempting to flash Custom Modem Shell (Attempt {attempt + 1}/{retries})...")
            with HighLevel.DebugProbe(api, serial_number) as probe:
                probe.erase()
                time.sleep(erase_wait)
                
                # Create program options
                program_options = HighLevel.ProgramOptions(
                    verify=HighLevel.VerifyAction.VERIFY_READ,
                    erase_action=HighLevel.EraseAction.ERASE_ALL,
                    qspi_erase_action=HighLevel.EraseAction.ERASE_NONE,
                    reset=HighLevel.ResetAction.RESET_SYSTEM
                )
                
                probe.program(hex_file_path, program_options)
                time.sleep(program_wait)
                
                # Add a proper reset sequence
                probe.reset()
                time.sleep(1)  # Give device time to stabilize after reset
            print("Custom Modem Shell successfully installed.")
            return True
        except APIError as e:
            print(f"Error: {e}")
            if attempt < retries - 1:
                if interactive:
                    retry = input("Do you want to retry flashing Custom Modem Shell? (y/n): ").lower()
                    if retry != 'y':
                        return False
                else:
                    print(f"Auto-retrying... ({attempt + 2}/{retries})")
                    time.sleep(1)  # Brief pause before retry
            else:
                print("Max retries reached. Flashing failed.")
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
    """Send command and read response with improved handling"""
    try:
        # Clear any pending data
        jlink.rtt_read(0, buffer_size)
        
        # Send command
        if command:
            jlink.rtt_write(0, command.encode())
        
        # Read response with timeout
        response = ""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                data = jlink.rtt_read(0, buffer_size)
                if data:
                    # Handle both list and bytes types
                    decoded = ''.join(chr(x) for x in data) if isinstance(data, list) else data.decode('utf-8', errors='ignore')
                    response += decoded
                    
                    # Check for completion conditions
                    if 'OK' in response or 'ERROR' in response or 'mosh:~$' in response:
                        return response
                        
            except Exception as e:
                print(f"Read error: {e}")
            time.sleep(0.1)
            
        return response
        
    except Exception as e:
        print(f"Command error: {e}")
        return ""

def analyze_network_connection(response):
    if '+CEREG: 0,1' in response or '+CEREG: 0,5' in response:
        return True, "Network registered"
    return False, "Network not registered"

def analyze_location(response):
    match = re.search(r'method: (\w+).*?latitude: ([-+]?\d+\.\d+).*?longitude: ([-+]?\d+\.\d+).*?accuracy: (\d+\.\d+) m', response, re.DOTALL)
    if match:
        method, lat, lon, accuracy = match.groups()
        return True, f"{method} location: Lat {lat}, Lon {lon}, Accuracy {accuracy}m"
    return False, "Failed to get location"

def analyze_wifi_status(response):
    """Analyze WiFi connection status"""
    print("\nWiFi Test Response:")
    print("-------------------")
    print(f"Command sent: wifi connect")
    print(f"Response received:\n{response}")
    
    if 'Successfully connected to Wi-Fi network' in response:
        return True, "WiFi connected successfully"
    elif 'Failed to connect to Wi-Fi network' in response:
        return False, "WiFi connection failed"
    else:
        return False, "Unexpected response"

def analyze_wifi_scan(response, timeout=30):
    """Analyze WiFi scan results with longer wait"""
    print("\nWiFi Scan Test Response:")
    print("------------------------")
    print(f"Command sent: wifi scan")
    
    # Initial response check
    if "Scan requested" not in response:
        return False, "Scan request failed"
        
    # Wait for scan results
    start_time = time.time()
    while time.time() - start_time < timeout:
        response = send_command(jlink, "\r\n", timeout=2)
        print(f"Response received:\n{response}")
        
        # Look for scan completion indicators
        if "Scan results:" in response:
            if "SSID:" in response:
                return True, "Networks found"
            else:
                return True, "No networks found"
        elif "Scan failed" in response:
            return False, "Scan failed"
            
        time.sleep(2)
    
    return False, "Scan timeout"

def run_test(jlink, test_name, command, analyze_func):
    """Run test with improved response handling"""
    print(f"\nExecuting {test_name}...")
    print(f"Command to be sent: {command}")
    
    try:
        # Send command and get initial response
        initial_response = send_command(jlink, command, timeout=5)
        
        # For WiFi scan, we need extended analysis
        if test_name == "WiFi Scan":
            success, result = analyze_wifi_scan(initial_response)
        else:
            success, result = analyze_func(initial_response)
            
        print("\nTest Results:")
        print("-------------")
        if success:
            print(Fore.GREEN + f"{test_name}: SUCCESS - {result}")
        else:
            print(Fore.RED + f"{test_name}: FAILED - {result}")
        return success
        
    except Exception as e:
        print(Fore.RED + f"{test_name}: ERROR - {str(e)}")
        return False

def verify_rtt_communication(jlink):
    """Verify RTT communication is working with improved timing"""
    print("\nVerifying RTT communication...")
    try:
        # Initial reset and wait
        jlink.reset(halt=True)
        time.sleep(2)
        jlink.reset(halt=False)
        time.sleep(3)
        
        # Clear any pending data
        jlink.rtt_read(0, 1024)
        time.sleep(1)
        
        # Start AT command mode
        response = send_command(jlink, "at at_cmd_mode start\r\n", timeout=5)
        print("AT mode response:", response)
        
        # Check for successful AT mode start
        if 'MoSh AT command mode started' in response:
            print("AT command mode started successfully")
            
            # Now send test AT command
            response = send_command(jlink, "AT\r\n", timeout=5)
            print(f"AT command response:\n{response}")
            
            # The modem should respond with OK or ERROR
            return 'OK' in response or 'ERROR' in response
        else:
            print("Failed to start AT command mode")
            return False
            
    except Exception as e:
        print(f"RTT verification failed: {e}")
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

def main():
    # Modified test configurations with WiFi scan
    tests = [
        ("WiFi Scan", "wifi scan\r\n", analyze_wifi_scan),
        ("Network Status", "at at+cereg?\r\n", analyze_network_connection)
    ]

    api = HighLevel.API()
    jlink = None
    try:
        parser = argparse.ArgumentParser(description="Test script for nRF9160 custom PCBs")
        parser.add_argument("--hex", default="merged.hex", help="Path to the custom Modem Shell hex file")
        args = parser.parse_args()

        # Get the full path of the merged.hex file
        script_dir = os.path.dirname(os.path.abspath(__file__))
        hex_file_path = os.path.join(script_dir, args.hex)

        api.open()
        serial_number = get_serial_number(api)
        print(f"Using serial number: {serial_number}")

        # Flash the hex file first
        if not flash_custom_modem_shell(api, hex_file_path, serial_number):
            print("Flashing failed. Exiting.")
            return

        # Important: Wait longer after flashing
        print("Waiting for device to stabilize...")
        time.sleep(30)  # Initial wait after flash

        # Setup RTT with adjusted timeouts
        try:
            print("Setting up RTT connection...")
            jlink = setup_rtt(
                serial_number, 
                connection_timeout=10,
                rtt_timeout=120
            )
            
            # Wait after RTT setup
            time.sleep(5)
            
            print("\nVerifying RTT Communication")
            print("===========================")
            verification_success = False
            for attempt in range(3):
                if verify_rtt_communication(jlink):
                    print(Fore.GREEN + "RTT communication verified successfully")
                    verification_success = True
                    break
                print(f"Verification attempt {attempt + 1} failed, retrying...")
                time.sleep(5)
                jlink.reset(halt=False)  # Reset between attempts
                time.sleep(3)
                
            if not verification_success:
                print(Fore.RED + "Failed to establish reliable RTT communication")
                return

            # Only proceed with tests if RTT is verified
            if wait_for_device_stable(jlink):
                print("\nStarting Tests")
                print("==============")
                for test_name, command, analyze_func in tests:
                    run_test(jlink, test_name, command, analyze_func)
            else:
                print(Fore.RED + "Device failed to stabilize")
        except Exception as e:
            print(f"Failed to setup RTT: {e}")
            return

    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if jlink:
            try:
                jlink.rtt_stop()
                jlink.close()
            except Exception as e:
                print(f"Error during cleanup: {e}")
        api.close()

if __name__ == '__main__':
    main()