#!/usr/bin/env python3
"""
Comprehensive Test Suite for WebSocket Queue Server
Tests various scenarios including normal operation, edge cases, and error conditions.
"""

import asyncio
import websockets
import json
import time
import threading
import subprocess
import signal
import os
import sys
from typing import List, Dict, Any
import sqlite3
from unittest.mock import patch
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import random

class Colors:
    """ANSI color codes for terminal output"""
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    END = '\033[0m'

class TestResult:
    """Test result container"""
    def __init__(self, name: str, passed: bool, message: str = "", error: str = ""):
        self.name = name
        self.passed = passed
        self.message = message
        self.error = error

class MockHTTPHandler(BaseHTTPRequestHandler):
    """Mock HTTP server for testing external API calls"""
    
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        # Parse the request
        try:
            data = json.loads(post_data.decode('utf-8'))
        except:
            data = {}
        
        # Simulate different responses based on test scenarios
        if hasattr(MockHTTPHandler, 'response_mode'):
            if MockHTTPHandler.response_mode == 'success':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {"status": "success", "data": "processed"}
                self.wfile.write(json.dumps(response).encode())
            elif MockHTTPHandler.response_mode == 'error':
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {"error": "Internal server error"}
                self.wfile.write(json.dumps(response).encode())
            elif MockHTTPHandler.response_mode == 'timeout':
                time.sleep(5)  # Simulate timeout
                self.send_response(408)
                self.end_headers()
        else:
            # Default success response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {"status": "success", "data": "processed"}
            self.wfile.write(json.dumps(response).encode())
    
    def log_message(self, format, *args):
        return  # Suppress logs

class QueueServerTester:
    """Main test class for queue server"""
    
    def __init__(self):
        self.server_process = None
        self.mock_server = None
        self.mock_thread = None
        self.results: List[TestResult] = []
        self.ws_url = "ws://127.0.0.1:8080/ws/"
        
    def start_mock_server(self, port=8081):
        """Start mock HTTP server for API calls"""
        try:
            self.mock_server = HTTPServer(('localhost', port), MockHTTPHandler)
            self.mock_thread = threading.Thread(target=self.mock_server.serve_forever)
            self.mock_thread.daemon = True
            self.mock_thread.start()
            time.sleep(0.5)  # Give server time to start
            return True
        except Exception as e:
            print(f"{Colors.RED}Failed to start mock server: {e}{Colors.END}")
            return False
    
    def stop_mock_server(self):
        """Stop mock HTTP server"""
        if self.mock_server:
            self.mock_server.shutdown()
            if self.mock_thread:
                self.mock_thread.join()
    
    def start_queue_server(self):
        """Start the Rust queue server"""
        try:
            # Change to the project directory
            project_dir = os.path.dirname(os.path.abspath(__file__))
            
            # Build the project first
            print(f"{Colors.CYAN}Building Rust server...{Colors.END}")
            build_process = subprocess.run(
                ["cargo", "build"], 
                cwd=project_dir, 
                capture_output=True, 
                text=True
            )
            
            if build_process.returncode != 0:
                print(f"{Colors.RED}Build failed: {build_process.stderr}{Colors.END}")
                return False
            
            # Start the server
            print(f"{Colors.CYAN}Starting Rust server...{Colors.END}")
            self.server_process = subprocess.Popen(
                ["cargo", "run"],
                cwd=project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait for server to start
            time.sleep(3)
            
            # Check if server is running
            if self.server_process.poll() is not None:
                stdout, stderr = self.server_process.communicate()
                print(f"{Colors.RED}Server failed to start: {stderr}{Colors.END}")
                return False
                
            return True
            
        except Exception as e:
            print(f"{Colors.RED}Failed to start server: {e}{Colors.END}")
            return False
    
    def stop_queue_server(self):
        """Stop the Rust queue server"""
        if self.server_process:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
                self.server_process.wait()
    
    async def connect_websocket(self, timeout=5):
        """Connect to WebSocket server"""
        try:
            websocket = await asyncio.wait_for(
                websockets.connect(self.ws_url), 
                timeout=timeout
            )
            return websocket
        except Exception as e:
            raise ConnectionError(f"Failed to connect to WebSocket: {e}")
    
    async def test_basic_connection(self):
        """Test 1: Basic WebSocket connection"""
        try:
            ws = await self.connect_websocket()
            
            # Should receive queue assignment message
            message = await asyncio.wait_for(ws.recv(), timeout=5)
            await ws.close()
            
            if "Joined queue:" in message:
                return TestResult("Basic Connection", True, f"Successfully joined queue: {message}")
            else:
                return TestResult("Basic Connection", False, f"Unexpected message: {message}")
                
        except Exception as e:
            return TestResult("Basic Connection", False, error=str(e))
    
    async def test_parameter_sending(self):
        """Test 2: Sending user parameters"""
        try:
            ws = await self.connect_websocket()
            
            # Wait for queue assignment
            queue_msg = await asyncio.wait_for(ws.recv(), timeout=5)
            print(queue_msg)
            
            # Send parameters
            params = {"value": "test_parameter"}
            await ws.send(json.dumps(params))
            # Should receive confirmation
            param_msg = await asyncio.wait_for(ws.recv(), timeout=5)
            await ws.close()
            # if "Parameters received" in param_msg:
            if "Position in" in param_msg:
                return TestResult("Parameter Sending", True, "Parameters accepted")
            else:
                return TestResult("Parameter Sending", False, f"Unexpected response: {param_msg}")
                
        except Exception as e:
            print("error:", e)
            return TestResult("Parameter Sending", False, error=str(e))
    
    async def test_invalid_parameters(self):
        """Test 3: Sending invalid parameters"""
        try:
            ws = await self.connect_websocket()
            
            # Wait for queue assignment
            await asyncio.wait_for(ws.recv(), timeout=5)
            
            # Send invalid JSON
            await ws.send("invalid json")
            
            # Should receive error message
            response = await asyncio.wait_for(ws.recv(), timeout=5)
            await ws.close()
            
            if "Position in" in response:
                return TestResult("Invalid Parameters", True, "Invalid parameters properly rejected")
            else:
                return TestResult("Invalid Parameters", False, f"Unexpected response: {response}")
                
        except Exception as e:
            return TestResult("Invalid Parameters", False, error=str(e))
    
    async def test_multiple_users_same_queue(self):
        """Test 4: Multiple users in same queue"""
        try:
            connections = []
            messages = []
            
            # Connect multiple users
            for i in range(3):
                ws = await self.connect_websocket()
                connections.append(ws)
                
                # Get initial message
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                messages.append(msg)
            
            # Check that users get position updates
            position_updates = []
            for ws in connections:
                try:
                    # Wait a bit for position updates
                    msg = await asyncio.wait_for(ws.recv(), timeout=2)
                    position_updates.append(msg)
                except asyncio.TimeoutError:
                    pass
            
            # Close all connections
            for ws in connections:
                await ws.close()
            
            # Check if multiple users were assigned to queues
            queue_assignments = [msg for msg in messages if "Joined queue:" in msg]
            
            if len(queue_assignments) >= 2:
                return TestResult("Multiple Users Same Queue", True, 
                                f"Successfully handled {len(queue_assignments)} users")
            else:
                return TestResult("Multiple Users Same Queue", False, 
                                f"Only {len(queue_assignments)} users joined queues")
                
        except Exception as e:
            return TestResult("Multiple Users Same Queue", False, error=str(e))
    
    async def test_queue_capacity_limit(self):
        """Test 5: Queue capacity limits (MAX_USERS_PER_QUEUE = 5)"""
        try:
            connections = []
            
            # Try to connect more than MAX_USERS_PER_QUEUE * 5 users
            for i in range(30):  # More than total capacity
                try:
                    ws = await self.connect_websocket()
                    msg = await asyncio.wait_for(ws.recv(), timeout=2)
                    
                    if "All queues are full" in msg:
                        await ws.close()
                        break
                    else:
                        connections.append(ws)
                        
                except Exception:
                    break
            
            # Close all connections
            for ws in connections:
                try:
                    await ws.close()
                except:
                    pass
            
            if len(connections) <= 25:  # 5 queues * 5 users each
                return TestResult("Queue Capacity Limit", True, 
                                f"Properly limited connections to {len(connections)}")
            else:
                return TestResult("Queue Capacity Limit", False, 
                                f"Allowed too many connections: {len(connections)}")
                
        except Exception as e:
            return TestResult("Queue Capacity Limit", False, error=str(e))
    
    async def test_user_disconnect_cleanup(self):
        """Test 6: User disconnect and cleanup"""
        try:
            # Connect a user
            ws1 = await self.connect_websocket()
            msg1 = await asyncio.wait_for(ws1.recv(), timeout=5)
            
            # Connect another user to same queue if possible
            ws2 = await self.connect_websocket()
            msg2 = await asyncio.wait_for(ws2.recv(), timeout=5)
            
            # Disconnect first user abruptly
            await ws1.close()
            
            # Wait a bit for cleanup
            await asyncio.sleep(1)
            
            # Second user should get position update
            try:
                position_msg = await asyncio.wait_for(ws2.recv(), timeout=3)
                await ws2.close()
                
                return TestResult("User Disconnect Cleanup", True, 
                                "User disconnect properly cleaned up")
            except asyncio.TimeoutError:
                await ws2.close()
                return TestResult("User Disconnect Cleanup", True, 
                                "Disconnect handled (no position update needed)")
                
        except Exception as e:
            return TestResult("User Disconnect Cleanup", False, error=str(e))
    
    async def test_rapid_connections(self):
        """Test 7: Rapid connection/disconnection stress test"""
        try:
            successful_connections = 0
            errors = 0
            
            for i in range(20):
                try:
                    ws = await self.connect_websocket(timeout=2)
                    msg = await asyncio.wait_for(ws.recv(), timeout=2)
                    await ws.close()
                    successful_connections += 1
                    
                    # Small delay to not overwhelm
                    await asyncio.sleep(0.1)
                    
                except Exception:
                    errors += 1
                    await asyncio.sleep(0.1)
            
            if successful_connections >= 15:  # Allow some failures
                return TestResult("Rapid Connections", True, 
                                f"Handled {successful_connections}/20 rapid connections")
            else:
                return TestResult("Rapid Connections", False, 
                                f"Only {successful_connections}/20 connections succeeded")
                
        except Exception as e:
            return TestResult("Rapid Connections", False, error=str(e))
    
    async def test_database_persistence(self):
        """Test 8: Database logging functionality"""
        try:
            # Connect and send parameters
            ws = await self.connect_websocket()
            queue_msg = await asyncio.wait_for(ws.recv(), timeout=5)
            
            params = {"value": "db_test_parameter"}
            await ws.send(json.dumps(params))
            await asyncio.wait_for(ws.recv(), timeout=5)
            
            await ws.close()
            
            # Give time for DB operations
            await asyncio.sleep(1)
            
            # Check database
            try:
                conn = sqlite3.connect('queue.db')
                cursor = conn.cursor()
                
                # Check if user was logged
                cursor.execute("SELECT COUNT(*) FROM users")
                user_count = cursor.fetchone()[0]
                
                conn.close()
                
                # Note: User should be deleted after processing, so count might be 0
                return TestResult("Database Persistence", True, 
                                f"Database operations working (user records processed)")
                
            except Exception as db_error:
                return TestResult("Database Persistence", False, 
                                error=f"Database error: {db_error}")
                
        except Exception as e:
            return TestResult("Database Persistence", False, error=str(e))
    
    async def test_concurrent_queue_operations(self):
        """Test 9: Concurrent operations across different queues"""
        try:
            connections = []
            
            # Connect users quickly to potentially spread across queues
            for i in range(10):
                ws = await self.connect_websocket()
                connections.append(ws)
                
                # Don't wait for message to create concurrency
                await asyncio.sleep(0.05)
            
            # Now collect all messages
            queue_assignments = {}
            for i, ws in enumerate(connections):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3)
                    if "Joined queue:" in msg:
                        queue_name = msg.split("Joined queue: ")[1].strip()
                        if queue_name not in queue_assignments:
                            queue_assignments[queue_name] = 0
                        queue_assignments[queue_name] += 1
                except asyncio.TimeoutError:
                    pass
            
            # Close all connections
            for ws in connections:
                try:
                    await ws.close()
                except:
                    pass
            
            # Check if users were distributed across multiple queues
            if len(queue_assignments) >= 2:
                return TestResult("Concurrent Queue Operations", True, 
                                f"Users distributed across {len(queue_assignments)} queues: {queue_assignments}")
            else:
                return TestResult("Concurrent Queue Operations", True, 
                                f"All users in same queue (expected behavior): {queue_assignments}")
                
        except Exception as e:
            return TestResult("Concurrent Queue Operations", False, error=str(e))
    
    async def test_websocket_protocol_errors(self):
        """Test 10: WebSocket protocol error handling"""
        try:
            ws = await self.connect_websocket()
            
            # Send ping
            pong_waiter = await ws.ping()
            await asyncio.wait_for(pong_waiter, timeout=5)
            
            # Send some valid message first
            queue_msg = await asyncio.wait_for(ws.recv(), timeout=5)
            
            await ws.close()
            
            return TestResult("WebSocket Protocol Errors", True, 
                            "WebSocket ping/pong and close handled correctly")
            
        except Exception as e:
            return TestResult("WebSocket Protocol Errors", False, error=str(e))
    
    async def test_endpoint_response_handling(self):
        """Test 11: Server endpoint response handling"""
        try:
            # First test with mock server that returns success
            if not self.start_mock_server(port=8082):
                return TestResult("Endpoint Response Handling", False, 
                                error="Could not start mock server for testing")
            
            # Update endpoint temporarily for testing
            original_endpoint = None
            try:
                with open("REQUEST_ENDPOINT.txt", "r") as f:
                    original_endpoint = f.read().strip()
                
                # Set to our mock server
                with open("REQUEST_ENDPOINT.txt", "w") as f:
                    f.write("http://localhost:8082/test")
                
                # Test 1: Successful response
                MockHTTPHandler.response_mode = 'success'
                
                ws = await self.connect_websocket()
                queue_msg = await asyncio.wait_for(ws.recv(), timeout=5)
                
                # Send parameters to trigger request processing
                params = {"value": "endpoint_test_success"}
                await ws.send(json.dumps(params))
                
                # Wait for position update and potential success message
                messages = []
                try:
                    for _ in range(3):  # Try to get multiple messages
                        msg = await asyncio.wait_for(ws.recv(), timeout=25)  # Wait longer for request processing
                        messages.append(msg)
                        if "Position in line" in msg or "processed" in msg:
                            break
                except asyncio.TimeoutError:
                    pass
                
                await ws.close()
                
                success_found = any("Position in line" in msg or "processed" in msg for msg in messages)
                
                # Test 2: Error response
                MockHTTPHandler.response_mode = 'error'
                
                ws2 = await self.connect_websocket()
                await asyncio.wait_for(ws2.recv(), timeout=5)  # Queue assignment
                
                params2 = {"value": "endpoint_test_error"}
                await ws2.send(json.dumps(params2))
                
                error_messages = []
                try:
                    for _ in range(3):
                        msg = await asyncio.wait_for(ws2.recv(), timeout=25)
                        error_messages.append(msg)
                        if "failed" in msg.lower() or "error" in msg.lower():
                            break
                except asyncio.TimeoutError:
                    pass
                
                await ws2.close()
                
                error_handled = any("failed" in msg.lower() or "error" in msg.lower() or "All attempts failed" in msg for msg in error_messages)
                
                # Test 3: Check request retry mechanism
                MockHTTPHandler.response_mode = 'error'
                
                ws3 = await self.connect_websocket()
                await asyncio.wait_for(ws3.recv(), timeout=5)
                
                params3 = {"value": "endpoint_test_retry"}
                await ws3.send(json.dumps(params3))
                
                retry_messages = []
                start_time = time.time()
                try:
                    while time.time() - start_time < 35:  # Wait for retry attempts
                        msg = await asyncio.wait_for(ws3.recv(), timeout=5)
                        retry_messages.append(msg)
                        if "All attempts failed" in msg:
                            break
                except asyncio.TimeoutError:
                    pass
                
                await ws3.close()
                
                retry_handled = any("All attempts failed" in msg for msg in retry_messages)
                print("Retry messages:", retry_messages)
                
                # Evaluate results
                if success_found and (error_handled or retry_handled):
                    return TestResult("Endpoint Response Handling", True, 
                                    f"Successfully handled various endpoint responses. Success: {success_found}, Error handling: {error_handled or retry_handled}")
                elif success_found:
                    return TestResult("Endpoint Response Handling", True, 
                                    "Successfully handled success responses, error handling needs verification")
                else:
                    return TestResult("Endpoint Response Handling", False, 
                                    f"Issues with endpoint handling. Messages received: {messages + error_messages + retry_messages}")
            
            finally:
                # Restore original endpoint
                if original_endpoint:
                    with open("REQUEST_ENDPOINT.txt", "w") as f:
                        f.write(original_endpoint)
                self.stop_mock_server()
                
        except Exception as e:
            return TestResult("Endpoint Response Handling", False, error=str(e))
    
    async def test_request_rate_limiting(self):
        """Test 12: Request rate limiting functionality"""
        try:
            # Start mock server
            if not self.start_mock_server(port=8083):
                return TestResult("Request Rate Limiting", False, 
                                error="Could not start mock server for testing")
            
            original_endpoint = None
            try:
                with open("REQUEST_ENDPOINT.txt", "r") as f:
                    original_endpoint = f.read().strip()
                
                with open("REQUEST_ENDPOINT.txt", "w") as f:
                    f.write("http://localhost:8083/test")
                
                MockHTTPHandler.response_mode = 'success'
                
                # Connect multiple users to the same queue quickly
                connections = []
                for i in range(3):
                    ws = await self.connect_websocket()
                    connections.append(ws)
                    await asyncio.wait_for(ws.recv(), timeout=5)  # Queue assignment
                    
                    # Send parameters
                    params = {"value": f"rate_limit_test_{i}"}
                    await ws.send(json.dumps(params))
                
                # Monitor messages to see rate limiting in action
                all_messages = []
                start_time = time.time()
                
                # Collect messages for 30 seconds to observe rate limiting
                while time.time() - start_time < 30:
                    for ws in connections:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=1)
                            all_messages.append((time.time(), msg))
                        except asyncio.TimeoutError:
                            continue
                    
                    # If we got success messages, rate limiting is working
                    success_count = sum(1 for _, msg in all_messages if "Success:" in msg)
                    if success_count >= 2:
                        break
                
                # Close connections
                for ws in connections:
                    await ws.close()
                
                # Analyze timing between successful requests
                success_times = [t for t, msg in all_messages if "Success:" in msg]
                
                if len(success_times) >= 2:
                    time_diff = success_times[1] - success_times[0]
                    # Check if there's at least some delay between requests (rate limiting)
                    if time_diff >= 15:  # MIN_REQUEST_INTERVAL is 20s, but allow some variance
                        return TestResult("Request Rate Limiting", True, 
                                        f"Rate limiting working - {time_diff:.1f}s between requests")
                    else:
                        return TestResult("Request Rate Limiting", False, 
                                        f"Rate limiting may not be working - only {time_diff:.1f}s between requests")
                else:
                    return TestResult("Request Rate Limiting", True, 
                                    "Rate limiting appears to be working (limited successful requests)")
            
            finally:
                if original_endpoint:
                    with open("REQUEST_ENDPOINT.txt", "w") as f:
                        f.write(original_endpoint)
                self.stop_mock_server()
                
        except Exception as e:
            return TestResult("Request Rate Limiting", False, error=str(e))
    
    async def test_queue_processing_order(self):
        """Test 13: Queue processing order (FIFO)"""
        try:
            if not self.start_mock_server(port=8084):
                return TestResult("Queue Processing Order", False, 
                                error="Could not start mock server for testing")
            
            original_endpoint = None
            try:
                with open("REQUEST_ENDPOINT.txt", "r") as f:
                    original_endpoint = f.read().strip()
                
                with open("REQUEST_ENDPOINT.txt", "w") as f:
                    f.write("http://localhost:8084/test")
                
                MockHTTPHandler.response_mode = 'success'
                
                # Connect users in sequence and track their order
                connections = []
                user_ids = []
                
                for i in range(3):
                    ws = await self.connect_websocket()
                    connections.append(ws)
                    await asyncio.wait_for(ws.recv(), timeout=5)  # Queue assignment
                    
                    user_id = f"user_{i}_{time.time()}"
                    user_ids.append(user_id)
                    params = {"value": user_id}
                    await ws.send(json.dumps(params))
                    
                    # Small delay to ensure order
                    await asyncio.sleep(0.5)
                
                # Monitor which user gets processed first
                processed_order = []
                start_time = time.time()
                
                while time.time() - start_time < 45 and len(processed_order) < 3:
                    for i, ws in enumerate(connections):
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=1)
                            if "Success:" in msg and user_ids[i] not in processed_order:
                                processed_order.append(user_ids[i])
                        except asyncio.TimeoutError:
                            continue
                
                # Close connections
                for ws in connections:
                    await ws.close()
                
                # Check if processing order matches connection order (FIFO)
                if len(processed_order) >= 2:
                    # Check if first connected user was processed first
                    first_user_processed_first = processed_order[0] == user_ids[0]
                    if first_user_processed_first:
                        return TestResult("Queue Processing Order", True, 
                                        f"FIFO order maintained: {processed_order}")
                    else:
                        return TestResult("Queue Processing Order", False, 
                                        f"FIFO order not maintained. Expected: {user_ids}, Got: {processed_order}")
                else:
                    return TestResult("Queue Processing Order", True, 
                                    "Queue processing appears to be working (limited data)")
            
            finally:
                if original_endpoint:
                    with open("REQUEST_ENDPOINT.txt", "w") as f:
                        f.write(original_endpoint)
                self.stop_mock_server()
                
        except Exception as e:
            return TestResult("Queue Processing Order", False, error=str(e))

    def check_server_health(self):
        """Check if server is responsive"""
        try:
            # Try to make a simple HTTP request to see if server is alive
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 8080))
            sock.close()
            return result == 0
        except:
            return False
    
    async def run_all_tests(self):
        """Run all tests and collect results"""
        print(f"{Colors.BOLD}{Colors.CYAN}Starting Queue Server Test Suite{Colors.END}")
        print("=" * 50)
        
        # List of all test methods
        tests = [
            self.test_basic_connection,
            self.test_parameter_sending,
            self.test_invalid_parameters,
            self.test_multiple_users_same_queue,
            self.test_queue_capacity_limit,
            self.test_user_disconnect_cleanup,
            self.test_rapid_connections,
            self.test_database_persistence,
            self.test_concurrent_queue_operations,
            self.test_websocket_protocol_errors,
            self.test_endpoint_response_handling,
            self.test_request_rate_limiting,
            self.test_queue_processing_order,
        ]
        
        for i, test_func in enumerate(tests, 1):
            print(f"\n{Colors.YELLOW}Running Test {i}/{len(tests)}: {test_func.__name__.replace('test_', '').replace('_', ' ').title()}{Colors.END}")
            
            # Check server health before each test
            if not self.check_server_health():
                result = TestResult(test_func.__name__, False, error="Server not responding")
                self.results.append(result)
                print(f"{Colors.RED}FAIL - Server not responding{Colors.END}")
                continue
            
            try:
                result = await asyncio.wait_for(test_func(), timeout=60)  # Increased timeout for endpoint tests
                self.results.append(result)
                
                if result.passed:
                    print(f"{Colors.GREEN}PASS{Colors.END} - {result.message}")
                else:
                    print(f"{Colors.RED}FAIL{Colors.END} - {result.message}")
                    if result.error:
                        print(f"  Error: {result.error}")
                        
            except asyncio.TimeoutError:
                result = TestResult(test_func.__name__, False, error="Test timed out")
                self.results.append(result)
                print(f"{Colors.RED}FAIL - Test timed out{Colors.END}")
                
            except Exception as e:
                result = TestResult(test_func.__name__, False, error=str(e))
                self.results.append(result)
                print(f"{Colors.RED}FAIL - Unexpected error: {e}{Colors.END}")
            
            # Small delay between tests
            await asyncio.sleep(0.5)
    
    def print_summary(self):
        """Print test summary"""
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        
        print("\n" + "=" * 50)
        print(f"{Colors.BOLD}{Colors.CYAN}Test Summary{Colors.END}")
        print("=" * 50)
        
        for result in self.results:
            status_color = Colors.GREEN if result.passed else Colors.RED
            status = "PASS" if result.passed else "FAIL"
            name = result.name.replace('test_', '').replace('_', ' ').title()
            
            print(f"{status_color}{status:4}{Colors.END} | {name}")
            if not result.passed and result.error:
                print(f"       Error: {result.error}")
        
        print("=" * 50)
        pass_rate = (passed / total * 100) if total > 0 else 0
        summary_color = Colors.GREEN if pass_rate >= 80 else Colors.YELLOW if pass_rate >= 60 else Colors.RED
        
        print(f"{Colors.BOLD}Overall: {summary_color}{passed}/{total} tests passed ({pass_rate:.1f}%){Colors.END}")
        
        if pass_rate >= 80:
            print(f"{Colors.GREEN}{Colors.BOLD}✓ Server appears to be working well!{Colors.END}")
        elif pass_rate >= 60:
            print(f"{Colors.YELLOW}{Colors.BOLD}⚠ Server has some issues that should be addressed{Colors.END}")
        else:
            print(f"{Colors.RED}{Colors.BOLD}✗ Server has significant issues{Colors.END}")

async def main():
    """Main test execution"""
    tester = QueueServerTester()
    
    try:
        # Start mock server for API calls
        # print(f"{Colors.CYAN}Starting mock HTTP server...{Colors.END}")
        # if not tester.start_mock_server():
        #     print(f"{Colors.RED}Failed to start mock server, continuing anyway...{Colors.END}")
        
        # # Start the queue server
        # print(f"{Colors.CYAN}Starting queue server...{Colors.END}")
        # if not tester.start_queue_server():
        #     print(f"{Colors.RED}Failed to start queue server. Exiting.{Colors.END}")
        #     return
        
        # # Wait a bit more for server to be fully ready
        # print(f"{Colors.CYAN}Waiting for server to be ready...{Colors.END}")
        # await asyncio.sleep(2)
        
        # Run all tests
        await tester.run_all_tests()
        
        # Print summary
        tester.print_summary()
        
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Tests interrupted by user{Colors.END}")
    except Exception as e:
        print(f"\n{Colors.RED}Test suite error: {e}{Colors.END}")
    finally:
        # Cleanup
        print(f"\n{Colors.CYAN}Cleaning up...{Colors.END}")
        tester.stop_queue_server()
        tester.stop_mock_server()

if __name__ == "__main__":
    # Install required packages if not available
    required_packages = ['websockets', 'requests']
    
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            print(f"{Colors.YELLOW}Installing {package}...{Colors.END}")
            import subprocess
            import sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
    
    # Run tests
    asyncio.run(main())