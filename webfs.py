import os
import sys
import stat
import time
import shutil
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import mimetypes

# Set root directory to current working directory
ROOT_DIR = os.getcwd()

def get_permissions(path):
    """Get file/directory permission string in rwx format"""
    try:
        mode = os.stat(path, follow_symlinks=True).st_mode
        # Check owner permissions
        is_dir = stat.S_ISDIR(mode)
        perms = [
            'r' if mode & stat.S_IRUSR else '-',
            'w' if mode & stat.S_IWUSR else '-',
            'x' if (mode & stat.S_IXUSR) or (is_dir and (mode & stat.S_IXUSR == 0)) else '-'
        ]
        # Ensure directories have execute permission
        if is_dir and perms[2] == '-':
            perms[2] = 'x'
        return ''.join(perms)
    except Exception:
        return '---'

def resolve_final_path(path):
    """Resolve symlinks and return final target path and stat"""
    try:
        # Get symlink stat first
        st = os.lstat(path)
        if not stat.S_ISLNK(st.st_mode):
            return path, st
        
        # Resolve final target
        real_path = os.path.realpath(path)
        if os.path.exists(real_path):
            return real_path, os.stat(real_path, follow_symlinks=True)
        return path, st  # Broken symlink, use symlink itself
    except Exception:
        return path, os.stat(path, follow_symlinks=True)

def get_creation_time(path):
    """Get cross-platform file creation time (Unix timestamp)"""
    try:
        st = os.stat(path, follow_symlinks=True)
        if sys.platform == 'win32':
            return st.st_ctime
        # macOS/BSD
        if hasattr(st, 'st_birthtime'):
            return st.st_birthtime
        # Linux fallback to ctime
        return st.st_ctime
    except Exception:
        return 0.0

def sanitize_path(path):
    """Safely process path to prevent directory traversal"""
    # Decode and normalize path
    decoded = urllib.parse.unquote(path)
    normalized = os.path.normpath(decoded).lstrip('/')
    full_path = os.path.join(ROOT_DIR, normalized)
    
    # Verify path is within root directory
    if not os.path.commonpath([ROOT_DIR, full_path]) == ROOT_DIR:
        raise ValueError("Directory traversal forbidden")
    
    return full_path, normalized

def parse_range_header(header):
    """Parse Range header and return (start, end) or None"""
    if not header or not header.startswith('bytes='):
        return None
    range_str = header[6:].strip()
    if '-' not in range_str:
        return None
    
    start_str, end_str = range_str.split('-', 1)
    try:
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else None
        return (start, end)
    except ValueError:
        return None

def parse_query_params(query):
    """Parse URL query parameters"""
    if not query:
        return {}
    return urllib.parse.parse_qs(query)

class CustomHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.handle_request('GET')
    
    def do_HEAD(self):
        self.handle_request('HEAD')
    
    def do_POST(self):
        self.handle_request('POST')
    
    def do_PUT(self):
        self.handle_request('PUT')
    
    def do_PATCH(self):
        self.handle_request('PATCH')
    
    def do_DELETE(self):
        self.handle_request('DELETE')
    
    def handle_request(self, method):
        """Unified handler for all HTTP requests"""
        try:
            # Parse path and parameters
            parsed = urllib.parse.urlparse(self.path)
            full_path, rel_path = sanitize_path(parsed.path)
            query_params = parse_query_params(parsed.query)
            
            # Handle different HTTP methods
            if method in ('GET', 'HEAD'):
                self.handle_get_head(full_path, rel_path, query_params, method)
            elif method == 'POST':
                self.handle_post(full_path, query_params)
            elif method == 'PUT':
                self.handle_put(full_path, query_params)
            elif method == 'PATCH':
                self.handle_patch(full_path, query_params)
            elif method == 'MKCOL':
                self.handle_mkcol(full_path, query_params)
            elif method == 'DELETE':
                self.handle_delete(full_path)
            else:
                self.send_error(501, f"Method not supported: {method}")
        except ValueError as e:
            self.send_error(403, str(e))
        except Exception as e:
            self.send_error(500, f"Server error: {str(e)}")
    
    def handle_get_head(self, full_path, rel_path, query_params, method):
        """Handle GET and HEAD requests"""
        if not os.path.exists(full_path):
            self.send_error(404, "Not found")
            return
        
        # Handle directories
        if os.path.isdir(full_path):
            self.send_directory_listing(full_path, method)
            return
        
        # Handle files
        try:
            file_size = os.path.getsize(full_path)
            start, end = self.get_range(full_path, query_params, file_size)
            
            # Set response headers
            self.send_response(206 if (start > 0 or end is not None) else 200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(end - start + 1 if end is not None else file_size - start))
            
            # Handle range requests
            if start > 0 or end is not None:
                range_str = f"bytes {start}-{end if end is not None else file_size-1}/{file_size}"
                self.send_header("Content-Range", range_str)
            
            self.send_header("Last-Modified", self.date_time_string(os.path.getmtime(full_path)))
            self.end_headers()
            
            # HEAD requests don't return body
            if method == "HEAD":
                return
            
            # Read file range
            with open(full_path, 'rb') as f:
                f.seek(start)
                chunk_size = 8192
                bytes_to_send = end - start + 1 if end is not None else file_size - start
                
                while bytes_to_send > 0:
                    chunk = f.read(min(chunk_size, bytes_to_send))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    bytes_to_send -= len(chunk)
        except Exception as e:
            self.send_error(500, f"File read error: {str(e)}")
    
    def handle_post(self, full_path, query_params):
        """Handle POST requests (save file)"""
        # Determine target path
        if os.path.isdir(full_path):
            name = query_params.get('name', [None])[0]
            if not name:
                self.send_error(400, "Directory requires name parameter")
                return
            target_path = os.path.join(full_path, name)
        else:
            target_path = full_path
        
        # Save file
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                self.send_error(400, "No content")
                return
            
            with open(target_path, 'wb') as f:
                while content_length > 0:
                    chunk_size = min(8192, content_length)
                    chunk = self.rfile.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    content_length -= len(chunk)
            
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except Exception as e:
            self.send_error(500, f"Save failed: {str(e)}")
    
    def handle_put(self, full_path, query_params):
        """Handle PUT requests (rename file or create directory)"""
        # Check if mkdir parameter is present to create directory
        mkdir_param = query_params.get('mkdir', [None])[0]
        if mkdir_param:
            # Create directory
            if not os.path.isdir(full_path):
                self.send_error(404, "Parent directory not found")
                return
            
            name = query_params.get('name', [None])[0]
            if not name:
                self.send_error(400, "name parameter required for mkdir")
                return
            
            # Prevent directory traversal in directory name
            if '/' in name or '\\' in name:
                self.send_error(400, "Invalid directory name")
                return
            
            new_dir = os.path.join(full_path, name)
            try:
                os.mkdir(new_dir)
                self.send_response(201)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except FileExistsError:
                self.send_error(409, "Directory already exists")
            except Exception as e:
                self.send_error(500, f"Creation failed: {str(e)}")
        else:
            # Rename operation
            if not os.path.exists(full_path):
                self.send_error(404, "Source file not found")
                return
            
            name = query_params.get('name', [None])[0]
            if not name:
                self.send_error(400, "name parameter required for rename")
                return
            
            # Prevent directory traversal in filename
            if '/' in name or '\\' in name:
                self.send_error(400, "Invalid filename")
                return
            
            dir_path = os.path.dirname(full_path)
            new_path = os.path.join(dir_path, name)
            
            try:
                os.rename(full_path, new_path)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except Exception as e:
                self.send_error(500, f"Rename failed: {str(e)}")
    
    def handle_patch(self, full_path, query_params):
        """Handle PATCH requests (modify file)"""
        if not os.path.isfile(full_path):
            self.send_error(404, "File not found")
            return
        
        try:
            file_size = os.path.getsize(full_path)
            start, end = self.get_range(full_path, query_params, file_size)
            
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                self.send_error(400, "No content")
                return
            
            data = self.rfile.read(content_length)
            write_size = min(len(data), end - start + 1) if end is not None else len(data)
            
            # Write to file
            with open(full_path, 'r+b') as f:
                f.seek(start)
                f.write(data[:write_size])
            
            self.send_response(204)  # No Content
            self.end_headers()
        except Exception as e:
            self.send_error(500, f"Modification failed: {str(e)}")
    
    def handle_delete(self, full_path):
        """Handle DELETE requests (delete file/directory)"""
        if not os.path.exists(full_path):
            self.send_error(404, "Not found")
            return
        
        try:
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
            else:
                os.remove(full_path)
            self.send_response(204)  # No Content
            self.end_headers()
        except Exception as e:
            self.send_error(500, f"Deletion failed: {str(e)}")
    
    def send_directory_listing(self, dir_path, method):
        """Generate directory listing (text/plain format)"""
        try:
            entries = os.listdir(dir_path)
            dir_content = []
            
            for entry in entries:
                if entry.startswith('.'):  # Skip hidden files
                    continue
                
                entry_path = os.path.join(dir_path, entry)
                final_path, st = resolve_final_path(entry_path)
                
                # Get permissions
                perms = get_permissions(final_path)
                
                # Determine type
                if stat.S_ISDIR(st.st_mode):
                    item_type = 'd'
                elif stat.S_ISREG(st.st_mode):
                    item_type = 'f'
                else:
                    continue  # Skip special files
                
                # Get size
                size = st.st_size if item_type == 'f' else 0
                
                # Get timestamps
                create_time = get_creation_time(final_path)
                mod_time = st.st_mtime
                
                # Format: permissions,type,size,creation_time,modification_time,filename
                dir_content.append(f"{perms},{item_type},{size},{create_time},{mod_time},{entry}")
            
            content = "\n".join(dir_content).encode('utf-8')
            
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            
            if method != "HEAD":
                self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Directory read error: {str(e)}")
    
    def get_range(self, file_path, query_params, file_size):
        """Get requested range (start, end)"""
        # Prefer Range header
        range_header = self.headers.get('Range')
        range_val = parse_range_header(range_header)
        
        # Fall back to query parameters
        if not range_val:
            offset = query_params.get('offset', [None])[0]
            length = query_params.get('length', [None])[0]
            
            if offset is not None:
                try:
                    start = int(offset)
                    end = start + int(length) - 1 if length else None
                    range_val = (max(0, start), end)
                except (TypeError, ValueError):
                    range_val = None
        
        # Default to entire file
        if not range_val:
            return 0, file_size - 1
        
        start, end = range_val
        start = max(0, min(start, file_size - 1))
        end = min(file_size - 1, end) if end is not None else file_size - 1
        
        return start, end
    
    # Disable logging
    def log_request(self, code='-', size='-'):
        pass

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server"""
    daemon_threads = True

def run_server(port=23542):
    server_address = ('0.0.0.0', port)
    httpd = ThreadedHTTPServer(server_address, CustomHTTPRequestHandler)
    print(f"Server running on port {port}, root directory: {ROOT_DIR}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.shutdown()

if __name__ == "__main__":
    run_server()
