import os
import sys
import importlib.util
import logging
import json
import time
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import re
from functools import lru_cache
import concurrent.futures

import requests
from faker import Faker

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("FastAPIParser")

fake = Faker()

# Ensure FastAPI is available.
try:
    from fastapi import FastAPI
    from fastapi.routing import APIRoute
except ImportError:
    FastAPI = None
    APIRoute = None

class FastAPIDiscoveryError(Exception):
    """Custom exception for FastAPI discovery failures."""
    pass

class FastAPIParseError(Exception):
    """Custom exception for OpenAPI parsing failures."""
    pass

def generate_dummy_value(field_schema: Dict[str, Any]) -> Any:
    """Generate a dummy value based on a JSON schema field definition with enhanced validation."""
    field_type = field_schema.get("type", "string")
    try:
        if field_type == "string":
            fmt = field_schema.get("format", "")
            if fmt == "email":
                return fake.email()
            elif fmt == "date":
                return fake.date()
            elif fmt == "date-time":
                return fake.iso8601()
            elif fmt == "uuid":
                return fake.uuid4()
            elif "enum" in field_schema:
                return fake.random_element(field_schema["enum"])
            elif "pattern" in field_schema:
                pattern = field_schema["pattern"]
                try:
                    return fake.regex(pattern)
                except Exception:
                    return fake.word()
            elif "minLength" in field_schema or "maxLength" in field_schema:
                min_len = field_schema.get("minLength", 1)
                max_len = field_schema.get("maxLength", min_len + 20)
                # Ensure the generated text is at least min_len characters long.
                return fake.text(max_nb_chars=max_len)[:max_len].ljust(min_len)
            else:
                return fake.word()
        elif field_type == "integer":
            return fake.random_int(
                min=field_schema.get("minimum", 0),
                max=field_schema.get("maximum", 100),
                step=field_schema.get("multipleOf", 1)
            )
        elif field_type == "number":
            return fake.pyfloat(
                left_digits=3,
                right_digits=2,
                positive=not field_schema.get("exclusiveMinimum", False),
                min_value=field_schema.get("minimum"),
                max_value=field_schema.get("maximum")
            )
        elif field_type == "boolean":
            return fake.boolean()
        elif field_type == "array":
            item_schema = field_schema.get("items", {"type": "string"})
            min_items = field_schema.get("minItems", 1)
            max_items = field_schema.get("maxItems", min_items + 2)
            unique = field_schema.get("uniqueItems", False)
            if unique:
                items = set()
                while len(items) < min_items:
                    items.add(generate_dummy_value(item_schema))
                return list(items)[:max_items]
            else:
                return [generate_dummy_value(item_schema)
                        for _ in range(fake.random_int(min=min_items, max=max_items))]
        elif field_type == "object":
            return generate_dummy_data_from_schema(field_schema)
    except Exception as e:
        logger.warning(f"Failed to generate dummy value for schema {field_schema}: {str(e)}")
        return None

def generate_dummy_data_from_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Generate dummy data from a JSON schema with enhanced error handling and validation."""
    try:
        dummy_data = {}
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])
        for prop, prop_schema in properties.items():
            if prop in required_fields or not required_fields:
                value = generate_dummy_value(prop_schema)
                if value is not None:
                    dummy_data[prop] = value
        # Handle additionalProperties if specified
        if schema.get("additionalProperties"):
            if isinstance(schema["additionalProperties"], dict):
                for _ in range(2):  # Add 2 additional properties
                    prop_name = fake.word()
                    if prop_name not in dummy_data:
                        dummy_data[prop_name] = generate_dummy_value(schema["additionalProperties"])
        return dummy_data
    except Exception as e:
        logger.error(f"Error generating dummy data from schema: {str(e)}")
        return {}

def is_valid_fastapi_app(app: Any) -> bool:
    """Check if an object is a valid FastAPI application instance with additional checks."""
    if FastAPI is None:
        return False
    if not isinstance(app, FastAPI) or not hasattr(app, 'openapi') or not callable(app.openapi):
        return False
    if not hasattr(app, 'routes') or not isinstance(app.routes, list):
        return False
    return True

@lru_cache(maxsize=10)
def discover_fastapi_app(root_dir: str, app_var_name: str = "app") -> Tuple[Optional[Any], Optional[str]]:
    """
    Recursively scan for FastAPI apps in Python files with caching and parallel scanning.
    Returns tuple of (app, module_path) or (None, None) if not found.
    """
    exclude_dirs = {".venv", "venv", "env", "__pycache__", ".git", "node_modules", "site-packages", "Lib"}
    app_candidates = []
    
    def scan_file(file_path: str):
        nonlocal app_candidates
        try:
            module_name = os.path.splitext(os.path.basename(file_path))[0]
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None:
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            # Check for the app variable (commonly "app")
            if hasattr(module, app_var_name):
                potential_app = getattr(module, app_var_name)
                if is_valid_fastapi_app(potential_app):
                    app_candidates.append((potential_app, file_path))
            # Also check for a common factory function "create_app"
            if hasattr(module, "create_app"):
                potential_app = getattr(module, "create_app")()
                if is_valid_fastapi_app(potential_app):
                    app_candidates.append((potential_app, file_path))
        except Exception as e:
            logger.debug(f"Skipping {file_path} due to error: {str(e)}")
    
    for curr_root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
        with concurrent.futures.ThreadPoolExecutor() as executor:
            python_files = [os.path.join(curr_root, f) for f in files if f.endswith(".py") and not f.startswith('_')]
            executor.map(scan_file, python_files)
        if app_candidates:
            logger.info(f"Found FastAPI app in {app_candidates[0][1]}")
            return app_candidates[0]
    return None, None

class FastAPIParser:
    """
    Enhanced FastAPI parser with:
    - Better error handling
    - Support for path‐level parameters (applies to all operations)
    - Caching and parallel processing
    """

    def __init__(self, root_dir: Optional[str] = None, app_var_name: str = "app"):
        self.root_dir = os.path.abspath(root_dir) if root_dir else os.getcwd()
        self.app_var_name = app_var_name
        self.app = None
        self.app_path = None
        self.openapi_schema = None
        self.endpoints = []
        self._discovery_cache = {}

    def discover_app(self, force_refresh: bool = False) -> bool:
        """Discover and load the FastAPI app with caching and retry."""
        cache_key = (self.root_dir, self.app_var_name)
        if not force_refresh and cache_key in self._discovery_cache:
            self.app, self.app_path = self._discovery_cache[cache_key]
            return self.app is not None
        try:
            self.app, self.app_path = discover_fastapi_app(self.root_dir, self.app_var_name)
            self._discovery_cache[cache_key] = (self.app, self.app_path)
            if self.app is None:
                raise FastAPIDiscoveryError(
                    f"No FastAPI app found in {self.root_dir}. Ensure your project contains a FastAPI instance named '{self.app_var_name}' or a 'create_app()' factory function."
                )
            return True
        except Exception as e:
            logger.error(f"App discovery failed: {str(e)}")
            raise FastAPIDiscoveryError(f"Failed to discover FastAPI app: {str(e)}")

    def load_openapi_schema(self, retries: int = 3) -> None:
        """Load OpenAPI schema with a retry mechanism."""
        if self.app is None and not self.discover_app():
            return
        for attempt in range(retries):
            try:
                self.openapi_schema = self.app.openapi()
                if not isinstance(self.openapi_schema, dict):
                    raise FastAPIParseError("Invalid OpenAPI schema format")
                logger.info("OpenAPI schema loaded successfully")
                return
            except Exception as e:
                if attempt == retries - 1:
                    logger.error(f"OpenAPI schema loading failed after {retries} attempts: {str(e)}")
                    raise FastAPIParseError(f"Failed to load OpenAPI schema: {str(e)}")
                logger.warning(f"OpenAPI schema loading failed (attempt {attempt + 1}), retrying...")
                time.sleep(1)

    def parse_endpoints(self) -> List[Dict[str, Any]]:
        """Parse endpoints with comprehensive metadata and error handling."""
        if self.openapi_schema is None:
            self.load_openapi_schema()
        endpoints = []
        paths = self.openapi_schema.get("paths", {})
        # Process paths in parallel
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(self._parse_path, path, methods): path
                for path, methods in paths.items()
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    endpoints.extend(future.result())
                except Exception as e:
                    logger.warning(f"Skipping path due to error: {str(e)}")
        self.endpoints = endpoints
        return endpoints

    def _parse_path(self, path: str, methods: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse all methods for a single path, including path‐level parameters."""
        path_level_params = methods.get("parameters", [])
        path_endpoints = []
        for http_method, op_data in methods.items():
            if http_method == "parameters" or http_method.lower() not in {
                "get", "post", "put", "delete", "patch", "options", "head"
            }:
                continue
            # merge path‐level parameters into operation parameters
            combined = dict(op_data)
            combined_params = list(path_level_params) + op_data.get("parameters", [])
            combined["parameters"] = combined_params
            try:
                endpoint = self._parse_single_endpoint(path, http_method, combined)
                path_endpoints.append(endpoint)
            except Exception as e:
                logger.warning(f"Skipping {http_method.upper()} {path} due to error: {str(e)}")
        return path_endpoints

    def _parse_single_endpoint(self, path: str, method: str, op_data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a single endpoint with detailed metadata."""
        endpoint = {
            "method": method.upper(),
            "path": path,
            "description": op_data.get("summary") or op_data.get("description", ""),
            "operation_id": op_data.get("operationId", ""),
            "tags": op_data.get("tags", []),
            "headers": self._parse_headers(op_data),
            "params": self._parse_parameters(op_data.get("parameters", [])),
            "body": self._parse_request_body(op_data.get("requestBody", {})),
            "security": self._parse_security(op_data.get("security", [])),
            "deprecated": op_data.get("deprecated", False),
            "content_types": self._parse_content_types(op_data.get("requestBody", {}))
        }
        if "callbacks" in op_data:
            endpoint["callbacks"] = self._parse_callbacks(op_data["callbacks"])
        return endpoint

    # ... (other helper methods remain unchanged) ...

    
    def _parse_headers(self, op_data: Dict[str, Any]) -> Dict[str, str]:
        """Parse headers with content type detection and security defaults."""
        headers = {}
        if op_data.get("requestBody"):
            content_types = list(op_data["requestBody"].get("content", {}).keys())
            if content_types:
                headers["Content-Type"] = content_types[0]
        for security_req in op_data.get("security", []):
            for scheme in security_req:
                if scheme.lower() == "bearer":
                    headers["Authorization"] = "Bearer {{token}}"
                elif scheme.lower() == "basic":
                    headers["Authorization"] = "Basic {{credentials}}"
        return headers
    
    def _parse_parameters(self, parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Parse query/path parameters into a structured format."""
        params = {}
        for param in parameters:
            param_in = param.get("in")
            if param_in in {"query", "path", "header", "cookie"}:
                param_schema = param.get("schema", {})
                params[param["name"]] = {
                    "in": param_in,
                    "type": param_schema.get("type", "string"),
                    "required": param.get("required", False),
                    "description": param.get("description", ""),
                    "example": generate_dummy_value(param_schema),
                    "schema": param_schema
                }
        return params
    
    def _parse_request_body(self, request_body: Dict[str, Any]) -> Dict[str, Any]:
        """Parse request body with schema validation and multiple content types."""
        if not request_body:
            return {}
        content = request_body.get("content", {})
        body_data = {}
        for content_type, media_type in content.items():
            if "schema" in media_type:
                if content_type == "application/json":
                    body_data[content_type] = generate_dummy_data_from_schema(media_type["schema"])
                elif content_type in {"multipart/form-data", "application/x-www-form-urlencoded"}:
                    body_data[content_type] = self._parse_form_data(media_type["schema"])
                else:
                    body_data[content_type] = media_type["schema"]
        return body_data
    
    def _parse_form_data(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Generate form data from a schema."""
        form_data = {}
        properties = schema.get("properties", {})
        for prop, prop_schema in properties.items():
            prop_type = prop_schema.get("type", "string")
            if prop_type == "string" and prop_schema.get("format") == "binary":
                form_data[prop] = "(binary file)"
            else:
                form_data[prop] = generate_dummy_value(prop_schema)
        return form_data
    
    def _parse_security(self, security: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse security requirements."""
        security_schemes = []
        for requirement in security:
            for scheme_name, scopes in requirement.items():
                security_schemes.append({
                    "scheme": scheme_name,
                    "scopes": scopes
                })
        return security_schemes
    
    def _parse_content_types(self, request_body: Dict[str, Any]) -> List[str]:
        """Parse available content types from the request body."""
        if not request_body:
            return []
        return list(request_body.get("content", {}).keys())
    
    def _parse_callbacks(self, callbacks: Dict[str, Any]) -> Dict[str, Any]:
        """Parse callback operations."""
        parsed_callbacks = {}
        for callback_name, callback_paths in callbacks.items():
            parsed_callbacks[callback_name] = {}
            for path, methods in callback_paths.items():
                parsed_callbacks[callback_name][path] = {}
                for method, op_data in methods.items():
                    try:
                        parsed_callbacks[callback_name][path][method] = self._parse_single_endpoint(path, method, op_data)
                    except Exception as e:
                        logger.warning(f"Skipping callback {callback_name} {method} {path}: {str(e)}")
        return parsed_callbacks

class FastAPITester:
    """
    Enhanced API tester with support for file uploads, form data, cookies,
    a retry mechanism, and robust SSL handling.
    """
    
    def __init__(self, base_url: str = "", verify_ssl: bool = False, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.default_timeout = 30
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries
        
        # Configure the retry adapter for the session.
        retry_adapter = requests.adapters.HTTPAdapter(
            max_retries=max_retries,
            pool_connections=10,
            pool_maxsize=10
        )
        self.session.mount("http://", retry_adapter)
        self.session.mount("https://", retry_adapter)
    
    def test_endpoint(
        self,
        endpoint: Dict[str, Any],
        environment: Optional[Dict[str, Any]] = None,
        auth: Optional[Tuple[str, str]] = None,
        files: Optional[Dict[str, Any]] = None,
        cookies: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Test an endpoint with comprehensive error handling and detailed response processing.
        Returns a dictionary containing status, response data, and error info if any.
        """
        try:
            method = endpoint["method"].lower()
            url = self._build_url(endpoint["path"], environment)
            headers = self._process_headers(endpoint.get("headers", {}), environment)
            params = self._process_params(endpoint.get("params", {}))
            
            content_type = headers.get("Content-Type", "application/json")
            data, json_data, files_data = None, None, None
            
            if method in {"post", "put", "patch"}:
                if content_type == "application/json":
                    json_data = endpoint.get("body", {}).get(content_type, {})
                elif content_type in {"multipart/form-data", "application/x-www-form-urlencoded"}:
                    data = endpoint.get("body", {}).get(content_type, {})
                    if files:
                        files_data = files
            
            # Send request with a retry loop.
            for attempt in range(self.max_retries + 1):
                try:
                    response = self.session.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        json=json_data,
                        data=data,
                        files=files_data,
                        auth=auth,
                        cookies=cookies,
                        timeout=self.default_timeout,
                        verify=self.verify_ssl
                    )
                    break
                except requests.exceptions.RequestException as e:
                    if attempt == self.max_retries:
                        raise
                    logger.warning(f"Request failed (attempt {attempt + 1}), retrying...")
                    time.sleep(1)
            
            return self._process_response(response)
        except Exception as e:
            return {
                "error": str(e),
                "status_code": None,
                "success": False,
                "response_time": None
            }
    
    def _build_url(self, path: str, environment: Optional[Dict[str, Any]]) -> str:
        """Build the full URL, substituting environment-specific variables if necessary."""
        base_url = self.base_url
        if environment:
            base_url = environment.get("base_url", base_url)
        full_path = path
        if environment:
            for var_name, var_value in environment.items():
                if var_name != "base_url":
                    full_path = full_path.replace(f"{{{var_name}}}", str(var_value))
        return f"{base_url.rstrip('/')}/{full_path.lstrip('/')}"
    
    def _process_headers(self, headers: Dict[str, str], environment: Optional[Dict[str, Any]]) -> Dict[str, str]:
        """Substitute environment variables in headers using the {{variable}} syntax."""
        processed = headers.copy()
        if environment:
            for k, v in processed.items():
                if isinstance(v, str):
                    matches = re.findall(r"\{\{([^}]+)\}\}", v)
                    for var_name in matches:
                        if var_name in environment:
                            processed[k] = processed[k].replace(f"{{{{{var_name}}}}}", str(environment[var_name]))
        return processed
    
    def _process_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Process parameters and assign example values if missing."""
        processed = {}
        for param_name, param_info in params.items():
            if isinstance(param_info, dict):
                param_value = param_info.get("example")
                if param_value is None:
                    param_value = generate_dummy_value(param_info.get("schema", {}))
                processed[param_name] = param_value
            else:
                processed[param_name] = param_info
        return processed
    
    def _process_response(self, response: requests.Response) -> Dict[str, Any]:
        """Parse response into a standardized format with details for the UI."""
        try:
            json_data = response.json()
        except ValueError:
            json_data = None
        return {
            "status_code": response.status_code,
            "success": 200 <= response.status_code < 300,
            "response_time": response.elapsed.total_seconds() * 1000,
            "headers": dict(response.headers),
            "cookies": dict(response.cookies),
            "body": response.text,
            "json": json_data,
            "error": None,
            "history": [{
                "url": hist.url,
                "status_code": hist.status_code,
                "method": hist.method
            } for hist in response.history]
        }

def get_fastapi_endpoints(
    root_dir: Optional[str] = None,
    app_var_name: str = "app",
    force_refresh: bool = False
) -> List[Dict[str, Any]]:
    """
    Public interface for the UI to get endpoints.
    Handles discovery, parsing, and caching of endpoint definitions.
    """
    try:
        parser = FastAPIParser(root_dir, app_var_name)
        endpoints = parser.parse_endpoints()
        cache_file = os.path.join(root_dir or os.getcwd(), ".fastapi_parser_cache.json")
        if not force_refresh and os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as f:
                    cached_endpoints = json.load(f)
                if cached_endpoints:
                    return cached_endpoints
            except Exception as e:
                logger.warning(f"Failed to load cache: {str(e)}")
        with open(cache_file, "w") as f:
            json.dump(endpoints, f)
        return endpoints
    except Exception as e:
        logger.error(f"Endpoint parsing failed: {str(e)}")
        return []

def test_fastapi_endpoint(
    endpoint: Dict[str, Any],
    base_url: str,
    environment: Optional[Dict[str, Any]] = None,
    auth: Optional[Tuple[str, str]] = None,
    files: Optional[Dict[str, Any]] = None,
    cookies: Optional[Dict[str, str]] = None,
    verify_ssl: bool = False,
    max_retries: int = 3
) -> Dict[str, Any]:
    """
    Enhanced public interface for testing endpoints.
    Supports file uploads, cookies, and a retry mechanism.
    """
    try:
        tester = FastAPITester(base_url, verify_ssl=verify_ssl, max_retries=max_retries)
        return tester.test_endpoint(
            endpoint=endpoint,
            environment=environment,
            auth=auth,
            files=files,
            cookies=cookies
        )
    except Exception as e:
        logger.error(f"Endpoint testing failed: {str(e)}")
        return {
            "error": str(e),
            "status_code": None,
            "success": False,
            "response_time": None
        }
