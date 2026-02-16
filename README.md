# WebFS

## 1. Overview
This server is a lightweight, authentication-free HTTP service designed specifically for file operations. It listens on a specified port (default 23542) across all network interfaces, providing comprehensive filesystem operation interfaces including file transfer, directory management, and partial updates. The server is designed with **no authentication and no access controls**, intended exclusively for trusted internal network environments.

> **Critical Security Warning**:  
> This service has **no security mechanisms whatsoever** and will expose all files on the server. **Never** deploy on public internet or untrusted networks. Use only in isolated test/development environments.

## 2. System Requirements
- **Python Version**: 3.6+
- **Operating Systems**: Linux, macOS, Windows
- **Dependencies**: Standard library only (no additional installations required)
- **Permissions**: Execution account requires full read/write permissions on the working directory

## 3. Installation and Startup
### 3.1 Obtaining the Code
```bash
git clone https://github.com/Ntoskrnl2333/WebFS.git
```

### 3.2 Starting the Service
```bash
python server.py [port_number]  # Default port 23542
```

**Example**:
```bash
$ python server.py 23542
Server running on port 23542, root directory: /home/user/files
```

### 3.3 Stopping the Service
Press `Ctrl+C` for graceful shutdown.

## 4. Core Functionality Specifications

### 4.1 Base Configuration
- **Root Directory**: Current working directory at startup
- **Bind Address**: `0.0.0.0` (all network interfaces)
- **Default Port**: 23542
- **Character Encoding**: UTF-8
- **Concurrency**: Multi-threaded (independent thread per request)

### 4.2 Path Security Mechanisms
All path operations undergo strict validation:
1. URL decoding followed by path normalization
2. Enforced confinement within root directory (prevents `../` traversal attacks)
3. Filename filtering (forbids `/` and `\` characters)
4. Symbolic link resolution to final targets (doesn't expose link structure)

### 4.3 Supported HTTP Methods

#### `GET` / `HEAD` Requests
**Behavior**:
- **File Requests**:
  - Returns `Content-Type: application/octet-stream`
  - Supports range requests:
    - Via `Range: bytes=start-end` header
    - Via URL parameters `?offset=N&length=M`
  - Partial requests return 206 status code + `Content-Range` header
- **Directory Requests**:
  - Returns `Content-Type: text/plain; charset=utf-8`
  - Line format: `[permissions],[type],[size],[creation_time],[modification_time],[filename]`
    - **Permissions**: `rwx`/`r-x`/`---` (based on server process user permissions)
    - **Type**: `d`=directory, `f`=file (symbolic links resolve to final target)
    - **Size**: 0 for directories, byte count for files
    - **Time**: Unix timestamps (seconds)
    - **Filename**: Base name only (no path)
  - Hidden files (starting with `.`) are excluded

**HEAD Special Handling**:  
Returns identical HTTP headers as GET, but **omits message body**.

#### `POST` Request
**Behavior**:
- Saves request body as file
- **Target Path Logic**:
  - If request path is directory: Must include `name` parameter specifying filename
  - If request path is file: Directly overwrites the file
- Returns 201 Created on success

**Example**:
```bash
curl -X POST --data-binary "@file.bin" http://server:23542/upload_dir?name=newfile.bin
```

#### `PUT` Request
**Behavior**:
- **File Renaming**: If request includes `name` parameter but no `mkdir` parameter:
  - **Must** include `name` parameter for file renaming
  - Only allows renaming (doesn't permit directory movement)
  - Strictly validates new filename contains no path separators
  - Returns 200 OK on success
- **Directory Creation**: If request includes both `name` and `mkdir` parameters:
  - Creates subdirectory under **existing directory**
  - **Must** include `name` parameter specifying directory name
  - Strictly validates directory name contains no path separators
  - Prohibits recursive multi-level directory creation
  - Returns 201 Created on success

**Examples**:
```bash
# File renaming
curl -X PUT "http://server:23542/oldname?name=newname"

# Directory creation
curl -X PUT "http://server:23542/parent_dir?name=new_subdir&mkdir=1"
```

#### `PATCH` Request
**Behavior**:
- Partially updates file content
- Supports range writing (same mechanism as GET):
  - `Range` header or `offset`/`length` parameters
- Writes beyond end-of-file extend the file
- Returns 204 No Content on success

**Example**:
```bash
curl -X PATCH -H "Range: bytes=100-199" --data-binary "@patch.bin" http://server:23542/file.bin
```

#### `DELETE` Request
**Behavior**:
- Recursively deletes directories (including all contents)
- Deletes single files
- Returns 204 No Content on success
- Returns 404 for non-existent paths

### 4.4 Error Handling
| Status Code | Example Scenarios                     |
|-------------|---------------------------------------|
| 400         | Missing required parameters (e.g., PUT's name) |
| 403         | Path traversal attack detected        |
| 404         | File/directory does not exist         |
| 409         | Directory already exists (during MKCOL) |
| 500         | Server internal error (permissions/disk space) |
| 501         | Unsupported HTTP method               |

## 5. Security Architecture
### 5.1 Protective Measures
- **Path Traversal Protection**: All paths strictly confined to root directory
- **Symbolic Link Handling**: Automatically resolves to final targets (doesn't expose link metadata)
- **Filename Filtering**: Prohibits creating files/directories with path separators
- **Hidden File Protection**: Filters out `.`-prefixed files in directory listings

### 5.2 Unprotected Features (High Risk!)
- **No Authentication**: Any client with port access can execute all operations
- **No Rate Limiting**: Vulnerable to DoS attacks
- **No Encryption**: All data transmitted in plaintext
- **No Permission Isolation**: Uses full system permissions of server process

> **Mandatory Deployment Requirements**:  
> 1. Operate only in firewall-isolated internal networks  
> 2. Run server process with minimal-privilege account  
> 3. Root directory must not contain sensitive data  
> 4. Restrict access IP range via network-layer ACLs  

## 6. Advanced Configuration
### 6.1 Modifying Default Port
```bash
python server.py 8080  # Use port 8080
```

### 6.2 Changing Root Directory
```bash
cd /path/to/files && python /path/to/server.py
```

### 6.3 Log Control
- Access logging **disabled** by default (reduces I/O overhead)
- For debugging, uncomment `log_request` method:
  ```python
  # def log_request(self, code='-', size='-'):
  #    pass
  ```

## 7. Technical Details
### 7.1 Cross-Platform Compatibility
- **Creation Time**:
  - Windows: `st_ctime`
  - macOS/BSD: `st_birthtime`
  - Linux: `st_ctime` (as creation time approximation)
- **Permission Mapping**:
  - Directories automatically gain execute bit (even if system lacks x permission)
  - Only checks owner permissions (ignores group/others)

### 7.2 Performance Characteristics
- **Large File Support**: Streamed transfer (8KB chunks)
- **Concurrency Model**: Thread pool (independent thread per connection)
- **Memory Control**: Never fully loads large files into memory

### 7.3 Symbolic Link Handling
| Scenario               | Handling Method                     |
|------------------------|-------------------------------------|
| Valid link             | Resolves to final target for attributes |
| Invalid/dangling link  | Treated as link file's own attributes |
| Link outside root dir  | Treated as invalid path (403 Forbidden) |

## 8. Test Cases
### 8.1 Directory Listing (GET)
```bash
curl http://localhost:23542/
```
**Response**:
```
rwx,d,0,1672531200,1672531200,docs
rw-,f,2048,1672531200,1672617600,data.bin
```

### 8.2 Partial File Download (GET + Range)
```bash
curl -H "Range: bytes=100-199" http://localhost:23542/large.bin -o partial.bin
```
**Response Headers**:
```
HTTP/1.0 206 Partial Content
Content-Type: application/octet-stream
Content-Range: bytes 100-199/10240
Content-Length: 100
```

### 8.3 Directory Creation (PUT)
```bash
curl -X PUT "http://localhost:23542/?name=assets&mkdir=1"
```
**Response**: `HTTP/1.0 201 Created`

## 9. Limitations and Warnings
1. **No Concurrent Write Protection**: Simultaneous writes to same file may corrupt data
2. **No Disk Space Checks**: Writes may fail with 500 errors when disk full
3. **No Timeout Controls**: Large file transfers may hold connections indefinitely
4. **Windows Path Limitations**: Paths exceeding 260 characters may fail
5. **Special Files Skipped**: Device files/named pipes are ignored in directory listings

---

**Final Reminder**: This software and this document(except this line and some small edits) was fully designed by Qwen and it's published with the Unlicense.
