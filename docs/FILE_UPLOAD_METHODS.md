# File Upload Methods Documentation

This document explains the different methods for uploading files to the `/medilocker/prescription` endpoint, including the previous approach, current implementation, and alternative methods.

## Table of Contents
1. [Previous Approach: Base64-encoded JSON](#previous-approach-base64-encoded-json)
2. [Current Approach: Multipart/Form-Data](#current-approach-multipartform-data)
3. [Alternative Methods](#alternative-methods)
4. [Comparison](#comparison)

---

## Previous Approach: Base64-encoded JSON

### How It Worked

Files were converted to base64 strings and sent as JSON in the request body.

#### Frontend Implementation (Before)

```javascript
// In Prescription.jsx
const extractFromFiles = async (files) => {
  // Convert files to base64 format
  const filesWithBase64 = await Promise.all(
    files.map(async (file) => {
      const base64Content = await fileToBase64(file);
      return {
        filename: file.name,
        content: base64Content,  // Base64 string
      };
    })
  );
  
  // Send as JSON
  const result = await extractStructuredData(filesWithBase64);
};

// In MedilockerService.js
export const extractStructuredData = async (files) => {
  const apiUrl = `${medilocker_API}/prescription`;
  
  const requestBody = {
    files: files,  // Array of { filename, content: base64 }
  };
  
  const response = await fetch(apiUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(requestBody),
  });
};
```

#### Backend Implementation (Before)

```python
@router.post("/prescription")
async def extract_structured_data(body: ExtractionRequest):
    """
    Expected body format:
    {
        "files": [
            {
                "filename": "document.pdf",
                "content": "base64_encoded_string_here..."
            }
        ]
    }
    """
    # Decode base64 content
    for file_data in body.files:
        file_bytes = base64.b64decode(file_data.content)
        # Process file...
```

### Pros
- ✅ Simple to implement
- ✅ Works with standard JSON APIs
- ✅ Easy to debug (can see content in request)
- ✅ No special handling needed for binary data

### Cons
- ❌ **33% larger payload size** (base64 encoding increases size)
- ❌ **Memory intensive** (entire file must be loaded into memory twice)
- ❌ **Slower** (encoding/decoding overhead)
- ❌ **Not efficient for large files**
- ❌ Requires custom conversion logic on both frontend and backend

---

## Current Approach: Multipart/Form-Data

### How It Works

Files are sent directly as binary data using the standard `multipart/form-data` format, which is the native way browsers handle file uploads.

#### Frontend Implementation (Current)

```javascript
// In Prescription.jsx
const extractFromFiles = async (files) => {
  // No conversion needed - pass files directly
  const filesToUpload = files.map((file) => file);
  const result = await extractStructuredData(filesToUpload);
};

// In MedilockerService.js
export const extractStructuredData = async (files) => {
  const apiUrl = `${medilocker_API}/prescription`;
  
  // Create FormData for multipart/form-data upload
  const formData = new FormData();
  
  // Append each file to FormData
  files.forEach((file, index) => {
    if (file.uri) {
      // React Native file format
      formData.append('files', {
        uri: file.uri,
        name: file.name || `file_${index}`,
        type: file.type || 'application/octet-stream',
      });
    } else {
      // Web File object
      formData.append('files', file, file.name || `file_${index}`);
    }
  });
  
  const response = await fetch(apiUrl, {
    method: "POST",
    // Don't set Content-Type - browser sets it automatically with boundary
    body: formData,
  });
};
```

#### Backend Implementation (Current)

```python
@router.post("/prescription")
async def extract_structured_data(
    files: List[UploadFile] = File(...)
):
    """
    Accepts direct file uploads via multipart/form-data.
    FastAPI automatically handles the file parsing.
    """
    for file in files:
        file_bytes = await file.read()  # Direct binary read
        # Process file...
```

### Pros
- ✅ **Native browser support** (standard file upload format)
- ✅ **Smaller payload size** (no base64 encoding overhead)
- ✅ **Faster** (no encoding/decoding)
- ✅ **Memory efficient** (streaming possible)
- ✅ **Better for large files**
- ✅ **Standard HTTP method** (works with CDNs, proxies, etc.)
- ✅ **Backend framework support** (FastAPI handles it automatically)

### Cons
- ⚠️ Slightly more complex frontend code (FormData handling)
- ⚠️ Different handling for web vs React Native

---

## Alternative Methods

### 1. Binary Stream (Raw POST)

Send files as raw binary data in the request body.

#### Frontend
```javascript
const response = await fetch(apiUrl, {
  method: "POST",
  headers: {
    "Content-Type": "application/octet-stream",
  },
  body: fileBlob,  // Raw binary
});
```

#### Backend
```python
@router.post("/prescription")
async def extract_structured_data(file: bytes = Body(...)):
    # Process raw bytes
```

**Use Case**: Single file uploads, when you need maximum control.

**Limitations**: 
- Only works for single files
- No metadata (filename, type) without custom headers

---

### 2. Base64 in URL (Data URI)

Embed base64-encoded files directly in the URL (not recommended for API calls).

```javascript
const dataUri = `data:image/png;base64,${base64String}`;
// Only useful for displaying images, not API uploads
```

**Use Case**: Displaying images in HTML, not for API uploads.

---

### 3. Chunked Upload (Multipart with Chunks)

Split large files into chunks and upload sequentially.

```javascript
const chunkSize = 1024 * 1024; // 1MB chunks
for (let i = 0; i < file.size; i += chunkSize) {
  const chunk = file.slice(i, i + chunkSize);
  await uploadChunk(chunk, chunkIndex);
}
```

**Use Case**: Very large files (>100MB), unreliable connections.

**Pros**: 
- Resume capability
- Progress tracking
- Better error recovery

**Cons**: 
- Complex implementation
- Requires backend support for chunk assembly

---

### 4. Pre-signed URLs (S3-style)

Get a temporary upload URL from the backend, upload directly to storage.

```javascript
// 1. Request upload URL
const { uploadUrl } = await fetch('/api/get-upload-url');

// 2. Upload directly to storage
await fetch(uploadUrl, {
  method: 'PUT',
  body: file,
});

// 3. Notify backend
await fetch('/api/confirm-upload', { fileId });
```

**Use Case**: Large files, direct-to-cloud-storage uploads.

**Pros**: 
- Offloads server bandwidth
- Direct client-to-storage
- Scalable

**Cons**: 
- Requires cloud storage setup
- More complex flow

---

### 5. WebSocket Binary Transfer

Send files over WebSocket connection.

```javascript
const ws = new WebSocket('ws://api.example.com');
ws.send(fileBlob);  // Binary data
```

**Use Case**: Real-time file transfer, chat applications.

**Pros**: 
- Real-time
- Bidirectional
- Can stream

**Cons**: 
- More complex
- Not RESTful
- Connection management overhead

---

## Comparison

| Method | Payload Size | Speed | Complexity | Use Case |
|--------|-------------|-------|------------|----------|
| **Base64 JSON** (Old) | +33% larger | Slower | Low | Simple APIs |
| **Multipart/Form-Data** (Current) | Original size | Fast | Medium | Standard file uploads ✅ |
| **Binary Stream** | Original size | Fast | Low | Single file, raw binary |
| **Chunked Upload** | Original size | Medium | High | Large files, unreliable networks |
| **Pre-signed URLs** | Original size | Fast | High | Large files, cloud storage |
| **WebSocket** | Original size | Fast | High | Real-time, streaming |

---

## Recommendations

### For `/medilocker/prescription`:
✅ **Use Multipart/Form-Data** (Current implementation)
- Standard HTTP method
- Efficient and fast
- Works well for medical documents (PDFs, images)
- Supported by all browsers and frameworks

### For Other Use Cases:

- **Single small file (<1MB)**: Multipart/Form-Data or Binary Stream
- **Large files (>100MB)**: Chunked Upload or Pre-signed URLs
- **Real-time transfer**: WebSocket
- **Direct cloud storage**: Pre-signed URLs

---

## Migration Notes

### What Changed

1. **Frontend**: Removed base64 conversion, now uses FormData
2. **Backend**: Changed from JSON body to `UploadFile` parameters
3. **Payload**: Reduced by ~33% (no base64 encoding)
4. **Performance**: Faster uploads, less memory usage

### Backward Compatibility

⚠️ **Breaking Change**: The old base64 JSON format is no longer supported. All clients must be updated to use multipart/form-data.

---

## Code Examples

### Web (Browser)
```javascript
const formData = new FormData();
formData.append('files', fileInput.files[0]);
formData.append('files', fileInput.files[1]);

fetch('/api/upload', {
  method: 'POST',
  body: formData,  // Browser sets Content-Type automatically
});
```

### React Native
```javascript
const formData = new FormData();
formData.append('files', {
  uri: file.uri,
  name: file.name,
  type: file.type,
});

fetch('/api/upload', {
  method: 'POST',
  body: formData,
});
```

### cURL Example
```bash
curl -X POST "https://api.example.com/medilocker/prescription" \
  -F "files=@document1.pdf" \
  -F "files=@document2.jpg"
```

---

## References

- [MDN: FormData API](https://developer.mozilla.org/en-US/docs/Web/API/FormData)
- [FastAPI: File Upload](https://fastapi.tiangolo.com/tutorial/request-files/)
- [RFC 7578: multipart/form-data](https://tools.ietf.org/html/rfc7578)
