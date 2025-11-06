"""
Test script for Lightning Data Pipeline
Run this to verify your pipeline is working correctly
"""

import requests
import time
import sys

API_URL = "http://localhost:8000"

def test_health():
    """Test if API is responding."""
    print("\n1. Testing API health...")
    try:
        response = requests.get(f"{API_URL}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"   ✓ API is healthy")
            print(f"   ✓ Database connected")
            print(f"   ✓ Total strikes in database: {data['total_strikes']}")
            return True
        else:
            print(f"   ✗ API returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"   ✗ Failed to connect: {e}")
        print("   Make sure the API is running (docker-compose up -d)")
        return False

def test_ingestion_stats():
    """Test ingestion statistics."""
    print("\n2. Testing ingestion statistics...")
    try:
        response = requests.get(f"{API_URL}/ingestion/stats", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"   ✓ Total received: {data['total_received']}")
            print(f"   ✓ Total stored: {data['total_stored']}")
            print(f"   ✓ Success rate: {data['success_rate']}%")
            print(f"   ✓ Last strike: {data['last_strike_time']}")
            return data['total_stored'] > 0
        else:
            print(f"   ✗ API returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"   ✗ Failed: {e}")
        return False
    
def test_recent_strikes():
    """Test getting recent strikes."""
    print("\n3. Testing recent strikes endpoint...")
    try:
        response = requests.get(f"{API_URL}/strikes/recent?minutes=60&limit=10", timeout=5)
        if response.status_code == 200:
            strikes = response.json()
            print(f"   ✓ Found {len(strikes)} strikes in last 60 minutes")
            if strikes:
                strike = strikes[0]
                print(f"   ✓ Latest strike: {strike['latitude']:.4f}, {strike['longitude']:.4f} at {strike['strike_timestamp']}")
            return True
        else:
            print(f"   ✗ API returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"   ✗ Failed: {e}")
        return False

def test_nearby_strikes():
    """Test nearby strikes query."""
    print("\n4. Testing nearby strikes (San Diego area)...")
    try:
        # San Diego coordinates
        response = requests.get(
            f"{API_URL}/strikes/nearby?lat=32.7157&lon=-117.1611&radius=100&minutes=120&limit=10",
            timeout=5
        )
        if response.status_code == 200:
            strikes = response.json()
            print(f"   ✓ Found {len(strikes)} strikes within 100km")
            for strike in strikes[:3]:
                dist = strike.get('distance_km', 'N/A')
                print(f"     - {strike['latitude']:.4f}, {strike['longitude']:.4f} ({dist}km away)")
            return True
        else:
            print(f"   ✗ API returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"   ✗ Failed: {e}")
        return False

def test_stats():
    """Test statistics endpoint."""
    print("\n5. Testing strike statistics...")
    try:
        response = requests.get(f"{API_URL}/strikes/stats", timeout=5)
        if response.status_code == 200:
            stats = response.json()
            print(f"   ✓ Total strikes: {stats['total_strikes']}")
            print(f"   ✓ Time range: {stats['time_range_start']} to {stats['time_range_end']}")
            if stats['avg_latitude'] and stats['avg_longitude']:
                print(f"   ✓ Avg location: {stats['avg_latitude']:.2f}, {stats['avg_longitude']:.2f}")
            return True
        else:
            print(f"   ✗ API returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"   ✗ Failed: {e}")
        return False

def wait_for_data():
    """Wait for some data to be ingested."""
    print("\n⏳ Waiting for data to be ingested...")
    print("   (This may take 1-2 minutes if the service just started)")
    
    for i in range(30):
        try:
            response = requests.get(f"{API_URL}/ingestion/stats", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data['total_stored'] > 10:
                    print(f"\n   ✓ {data['total_stored']} strikes ingested!")
                    return True
        except:
            pass
        
        print(f"   Waiting... ({i+1}/30)", end='\r')
        time.sleep(2)
    
    print("\n   ⚠ Still waiting for data. Make sure ingestion service is running.")
    return False

def main():
    print("=" * 60)
    print("Lightning Data Pipeline - Test Suite")
    print("=" * 60)
    
    # Test 1: Health check
    if not test_health():
        print("\n❌ Pipeline is not running. Start it with: docker-compose up -d")
        sys.exit(1)
    
    # Test 2: Check ingestion stats
    if not test_ingestion_stats():
        # Wait for data to come in
        if not wait_for_data():
            print("\n⚠ No data ingested yet. Pipeline might still be connecting.")
    
    # Test 3-5: API endpoints
    test_recent_strikes()
    test_nearby_strikes()
    test_stats()
    
    print("\n" + "=" * 60)
    print("✅ Pipeline test complete!")
    print("=" * 60)
    print("\nUseful commands:")
    print("  View API docs: http://localhost:8000/docs")
    print("  View logs: docker-compose logs -f ingestion")
    print("  Stop pipeline: docker-compose down")
    print("  Query database: docker exec lightning_db psql -U lightning_user -d lightning")
    print("\nExample queries:")
    print(f"  curl '{API_URL}/strikes/recent?minutes=30&limit=5'")
    print(f"  curl '{API_URL}/strikes/nearby?lat=40.7128&lon=-74.0060&radius=50'")

if __name__ == "__main__":
    main()