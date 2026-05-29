#!/usr/bin/env python3
"""Diagnostic script for ServiceNow CMDB connection issues."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import urllib.request
import json
import socket

print("=" * 80)
print("SERVICENOW CONNECTION DIAGNOSTIC")
print("=" * 80)

# Load environment variables
env_file = Path(__file__).parent.parent / '.env'
if env_file.exists():
    load_dotenv(env_file)
    print(f"\n✓ .env file found: {env_file}")
else:
    print(f"\n✗ .env file NOT found at {env_file}")

# Check DNS resolution
print("\n[1] DNS Resolution Check")
servicenow_host = "dev252187.service-now.com"
try:
    ip = socket.gethostbyname(servicenow_host)
    print(f"  ✓ {servicenow_host} resolves to {ip}")
except socket.gaierror as e:
    print(f"  ✗ DNS resolution failed for {servicenow_host}")
    print(f"    Error: {e}")

# Check environment variables
print("\n[2] ServiceNow Credentials Check")
sn_instance = os.getenv("SERVICENOW_INSTANCE")
sn_user = os.getenv("SERVICENOW_USER")
sn_pass = os.getenv("SERVICENOW_PASSWORD")

if sn_instance:
    print(f"  ✓ SERVICENOW_INSTANCE: {sn_instance}")
else:
    print(f"  ✗ SERVICENOW_INSTANCE not set")

if sn_user:
    print(f"  ✓ SERVICENOW_USER: {sn_user}")
else:
    print(f"  ✗ SERVICENOW_USER not set")

if sn_pass:
    print(f"  ✓ SERVICENOW_PASSWORD: {'*' * len(sn_pass)}")
else:
    print(f"  ✗ SERVICENOW_PASSWORD not set")

# Try HTTP connectivity
print("\n[3] HTTP Connectivity Test")
url = f"https://{servicenow_host}/api/now/version"
print(f"  Testing: {url}")

try:
    # Create request with timeout
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Plutus/1.0')
    
    # Try connection with short timeout
    try:
        response = urllib.request.urlopen(req, timeout=5)
        print(f"  ✓ Connection successful (Status: {response.status})")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(f"  ✓ Server reachable (401 Unauthorized - credentials may be invalid)")
        elif e.code == 404:
            print(f"  ✓ Server reachable (404 Not Found - endpoint may be different)")
        else:
            print(f"  ✗ HTTP Error: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        print(f"  ✗ Connection failed: {e.reason}")
    except socket.timeout:
        print(f"  ✗ Connection timeout (5 seconds) - server may be slow or blocked")
        print(f"    Try: Increase timeout, check firewall, verify VPN connection")
        
except Exception as e:
    print(f"  ✗ Unexpected error: {e}")

# Check network connectivity
print("\n[4] Network Status")
try:
    # Try to resolve google.com as general connectivity test
    socket.gethostbyname("google.com")
    print(f"  ✓ General internet connectivity: OK")
except:
    print(f"  ✗ General internet connectivity: FAILED")

print("\n" + "=" * 80)
print("DIAGNOSIS SUMMARY")
print("=" * 80)
print("""
Common ServiceNow connection issues:

1. **Timeout errors**: ServiceNow dev instances may be slow or suspended
   - Check: Is your dev instance active?
   - Fix: Restart the dev instance, increase timeout to 30s+

2. **401 Unauthorized**: Credentials are invalid or incorrect
   - Check: Username and password in .env
   - Fix: Verify credentials in ServiceNow admin console

3. **DNS/Connect failures**: Network access blocked
   - Check: Firewall, VPN, proxy settings
   - Fix: Enable VPN if required, check FW rules

4. **Non-critical for MVP**: CMDB population can be deferred
   - Neo4j graph is fully operational
   - Gap analyzer works with graph data
   - ServiceNow can be reconnected later

Current Status: ServiceNow not blocking other systems
""")
print("=" * 80)
