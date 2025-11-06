import json
import websocket
from websocket import ABNF
from typing import Dict, List, Tuple
import re
import datetime

class BlitzortungDecoder:
    def __init__(self):
        self.substitutions = {
            # DIGITS - These are the most critical mappings
            b'\xc4\x88': b'0',
            b'\xc4\x89': b'1', 
            b'\xc4\x8a': b'2',
            b'\xc4\x8b': b'3',
            b'\xc4\x8c': b'4',
            b'\xc4\x8d': b'5',
            b'\xc4\x8e': b'6',
            b'\xc4\x8f': b'7',
            b'\xc4\x90': b'8',
            b'\xc4\x91': b'9',
            
            # Alternative digit mappings in 90s range
            b'\xc4\x92': b'2',
            b'\xc4\x93': b'3',
            b'\xc4\x94': b'4',
            b'\xc4\x95': b'5',
            b'\xc4\x96': b'6',
            b'\xc4\x97': b'7',
            b'\xc4\x98': b'8',
            b'\xc4\x99': b'9',
            b'\xc4\x9a': b'0',
            b'\xc4\x9b': b'1',
            
            # More digit alternatives in A0s range
            b'\xc4\xa0': b'0',
            b'\xc4\xa1': b'1',
            b'\xc4\xa2': b'2',
            b'\xc4\xa3': b'3',
            b'\xc4\xa4': b'4',
            b'\xc4\xa5': b'5',
            b'\xc4\xa6': b'6',
            b'\xc4\xa7': b'7',
            b'\xc4\xa8': b'8',
            b'\xc4\xa9': b'9',
            
            # JSON structural elements
            b'\xc4\x86': b': ',
            b'\xc4\x87': b'.',
            
            # Quote characters
            b'\xc4\xb8': b'"',
            b'\xc4\xb9': b'"',
            b'\xc4\xba': b'"',
            b'\xc4\xbb': b'"',
            b'\xc4\xbc': b'"',
            b'\xc4\xbd': b'"',
            b'\xc4\x9c': b'"',
            b'\xc4\x9d': b'"',
            b'\xc4\x9e': b'"',
            b'\xc4\x9f': b'"',
            b'\xc4\xb0': b'"',
            b'\xc4\xb1': b'"',
            b'\xc4\xb2': b'"',
            b'\xc4\xb3': b'"',
            b'\xc4\xb4': b'"',
            b'\xc4\xb5': b'"',
            
            # Special characters mapped to digits (improvement #2)
            b'\xc4\xac': b'',     # Ĭ
            b'\xc5\x86': b'6',    # ņ -> 6
            b'\xc4\xab': b'',     # ī
            b'\xc4\xaa': b'',     # Ī
            b'\xc4\xb6': b'',     # Ķ
            b'\xc5\x80': b'',     # ŀ
            b'\xc5\x84': b'4',    # ń -> 4
            b'\xc5\x81': b'',     # Ł
            b'\xc4\xad': b'',     # ĭ
            b'\xc5\x85': b'5',    # Ņ -> 5
            b'\xc5\x89': b'9',    # ŉ -> 9
            b'\xc5\x88': b'8',    # Ň -> 8
            b'\xc4\x80': b'',     # Ā
            b'\xc4\x8e': b'6',    # Ŏ -> 6 (was causing issues)
            b'\xc5\x9b': b'',     # ś
            b'\xc5\x8d': b'',     # ō
            b'\xc4\x81': b'',     # ā
            b'\xc4\x83': b'',     # ă
            b'\xc4\x85': b'',     # ą
            b'\xc4\xae': b'',     # Į (was showing in pol field)
            b'\xc4\xbe': b'',     # ľ (was showing in pol field)
        }
        
        self.debug_mode = True
        self.sample_count = 0
        self.successful_decodes = 0
        self.validation_warnings = 0

    def decode(self, data: bytes) -> str:
        """Decode with the corrected mappings."""
        result = bytearray(data)
        
        # Apply substitutions
        for compressed, original in self.substitutions.items():
            result = result.replace(compressed, original)
        
        try:
            decoded = result.decode('utf-8', errors='replace')
        except:
            decoded = result.decode('latin1', errors='replace')
        
        # Post-process to fix JSON structure issues
        decoded = self._fix_json_structure(decoded)
        
        return decoded

    def _fix_decimal_point_issues(self, coord_str: str) -> float:
        """Fix longitude values that have misplaced decimal points (improvement #1)."""
        try:
            value = float(coord_str)
            
            # If value is suspiciously large (> 1000), likely missing decimal point
            if abs(value) > 1000:
                # Try to infer correct decimal placement
                # Most longitude errors are in the millions, should be < 180
                str_val = str(abs(int(value)))
                
                # If it's 6+ digits, likely should be XX.XXXXXX format
                if len(str_val) >= 6:
                    # Try placing decimal after first 2-3 digits
                    if len(str_val) == 8:  # e.g., 16040089 -> 16.040089
                        corrected = float(str_val[:2] + '.' + str_val[2:])
                    elif len(str_val) == 7:  # e.g., 1613330 -> 16.13330
                        corrected = float(str_val[:2] + '.' + str_val[2:])
                    else:
                        # Try to place decimal to make value < 180
                        for i in range(1, len(str_val)):
                            test_val = float(str_val[:i] + '.' + str_val[i:])
                            if test_val <= 180:
                                corrected = test_val
                                break
                        else:
                            corrected = value
                    
                    # Restore sign
                    if value < 0:
                        corrected = -corrected
                    
                    return corrected
            
            return value
        except:
            return None

    def _validate_coordinates(self, lat: float, lon: float) -> tuple:
        """Validate coordinates and flag suspicious values (improvement #3)."""
        warnings = []
        
        if lat is not None:
            if abs(lat) > 90:
                warnings.append(f"Invalid latitude: {lat} (must be -90 to 90)")
        
        if lon is not None:
            if abs(lon) > 180:
                warnings.append(f"Invalid longitude: {lon} (must be -180 to 180)")
        
        return warnings

    def _fix_json_structure(self, text: str) -> str:
        """Reconstruct proper JSON from the malformed decoded text."""
        
        import re
        
        # Extract timestamp
        time_match = re.search(r'time[":]+(\d+)', text)
        time_val = time_match.group(1) if time_match else None
        
        # Extract latitude 
        lat_match = re.search(r'lat[:\s]*([0-9.-]+)', text)
        lat_val = float(lat_match.group(1)) if lat_match else None
        
        # Extract longitude with improved decimal point handling
        lon_match = re.search(r'lon[:\s]*([0-9.-]+)', text)
        if lon_match:
            lon_val = self._fix_decimal_point_issues(lon_match.group(1))
        else:
            lon_val = None
        
        # Extract altitude
        alt_match = re.search(r'"?al"?[:\s]*([0-9.-]+)', text)
        if alt_match:
            try:
                alt_val = int(float(alt_match.group(1)))
            except ValueError:
                alt_val = None
        else:
            alt_val = None
        
        # Extract polarity
        pol_match = re.search(r'pol[:\s]*"?([^"]+)"?', text)
        pol_val = pol_match.group(1) if pol_match and pol_match.group(1) != 'mds' else None
        
        # Extract MDS value
        mds_match = re.search(r'mds[:\s]*([0-9.-]+)', text)
        if mds_match:
            try:
                mds_val = int(float(mds_match.group(1)))
            except ValueError:
                mds_val = None
        else:
            mds_val = None
        
        # Extract MCG value
        mcg_match = re.search(r'mcg[:\s]*([0-9.-]+)', text)
        if mcg_match:
            try:
                mcg_val = int(float(mcg_match.group(1)))
            except ValueError:
                mcg_val = None
        else:
            mcg_val = None
        
        # Reconstruct as proper JSON
        result = "{"
        
        if time_val:
            result += f'"time": {time_val}'
            
        if lat_val is not None:
            if len(result) > 1:
                result += ", "
            result += f'"lat": {lat_val}'
            
        if lon_val is not None:
            if len(result) > 1:
                result += ", "
            result += f'"lon": {lon_val}'
            
        if alt_val is not None:
            if len(result) > 1:
                result += ", "
            result += f'"alt": {alt_val}'
            
        if pol_val and pol_val.strip():
            if len(result) > 1:
                result += ", "
            result += f'"pol": "{pol_val.strip()}"'
            
        if mds_val is not None:
            if len(result) > 1:
                result += ", "
            result += f'"mds": {mds_val}'
            
        if mcg_val is not None:
            if len(result) > 1:
                result += ", "
            result += f'"mcg": {mcg_val}'
        
        result += "}"
        
        return result

    def analyze_sample(self, raw: bytes, decoded: str):
        """Analyze each sample."""
        self.sample_count += 1
        
        print(f"\n--- Sample {self.sample_count} ---")
        print(f"Decoded: {decoded[:100]}{'...' if len(decoded) > 100 else ''}")
        
        try:
            # Try to parse as JSON
            obj = json.loads(decoded)
            self.successful_decodes += 1
            
            # Validate coordinates (improvement #3)
            lat = obj.get('lat')
            lon = obj.get('lon')
            validation_warnings = self._validate_coordinates(lat, lon)
            
            if validation_warnings:
                print("SUCCESS! ⚠️Lightning data (with warnings):")
                self.validation_warnings += 1
                for warning in validation_warnings:
                    print(f"   WARNING: {warning}")
            else:
                print("SUCCESS! ✅ Lightning data:")
            
            if 'time' in obj:
                timestamp = obj['time']
                if isinstance(timestamp, (int, float)):
                    try:
                        if timestamp > 1e15:
                            dt = datetime.datetime.fromtimestamp(timestamp / 1000000)
                        elif timestamp > 1e12:
                            dt = datetime.datetime.fromtimestamp(timestamp / 1000)
                        else:
                            dt = datetime.datetime.fromtimestamp(timestamp)
                        print(f"   Time: {dt} (timestamp: {timestamp})")
                    except (ValueError, OSError):
                        print(f"   Time: {timestamp} (raw timestamp)")
                else:
                    print(f"   Time: {timestamp}")
            
            if 'lat' in obj and 'lon' in obj:
                print(f"   Location: {obj['lat']}, {obj['lon']}")
            
            # Show all other fields
            other_fields = {k: v for k, v in obj.items() if k not in ['time', 'lat', 'lon']}
            if other_fields:
                print(f"   Other data: {other_fields}")
                
            return True
            
        except json.JSONDecodeError as e:
            print(f"JSON Error at position {e.pos}: {e.msg}")
            
            # Show the problematic area
            start = max(0, e.pos - 20)
            end = min(len(decoded), e.pos + 20)
            print(f"   Context: ...{decoded[start:e.pos]}[ERROR]{decoded[e.pos:end]}...")
            
            # Suggest what might be wrong
            self._analyze_error_context(decoded, e.pos)
            
            return False

    def _analyze_error_context(self, text: str, pos: int):
        """Analyze the error context to suggest fixes."""
        if pos < len(text):
            char = text[pos]
            print(f"   Problem character: '{char}' (ord: {ord(char)})")

    def print_statistics(self):
        """Print final statistics."""
        if self.sample_count > 0:
            success_rate = (self.successful_decodes / self.sample_count) * 100
            print(f"\nFinal Statistics:")
            print(f"   Samples processed: {self.sample_count}")
            print(f"   Successful decodes: {self.successful_decodes}")
            print(f"   Success rate: {success_rate:.1f}%")
            print(f"   Validation warnings: {self.validation_warnings}")

# Initialize decoder
decoder = BlitzortungDecoder()

def on_data(ws, data, opcode, fin):
    if opcode == ABNF.OPCODE_BINARY:
        raw = data
    else:
        if isinstance(data, str):
            raw = data.encode('utf-8', errors='replace')
        else:
            raw = data
    
    # Decode and analyze
    decoded = decoder.decode(raw)
    decoder.analyze_sample(raw, decoded)

def on_open(ws):
    print("Connected to Blitzortung!")
    print("Requesting lightning data...\n")
    ws.send('{"a":111}')

def on_error(ws, e):
    print(f"WebSocket error: {e}")

def on_close(ws, *args):
    print("\nConnection closed")
    decoder.print_statistics()

if __name__ == "__main__":
    print("Blitzortung Lightning Decoder")
    print("=" * 40)
    print("Decoding real-time lightning strike data...\n")
    
    websocket.enableTrace(False)
    ws = websocket.WebSocketApp(
        "wss://ws7.blitzortung.org/",
        on_open=on_open,
        on_data=on_data,
        on_error=on_error,
        on_close=on_close
    )
    
    try:
        ws.run_forever()
    except KeyboardInterrupt:
        print("\nDecoder stopped")
        decoder.print_statistics()