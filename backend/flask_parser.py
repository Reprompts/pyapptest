import os
import sys
import importlib.util
import logging
import json
import time
import re
import concurrent.futures
from functools import lru_cache
from typing import List, Dict, Any, Optional, Tuple

import requests
from faker import Faker
from flask import Flask  # Import Flask from the official package

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("FlaskParser")

fake = Faker()

class FlaskDiscoveryError(Exception):
    """Custom exception for Flask app discovery failures"""
    pass

class FlaskParseError(Exception):
    """Custom exception for Flask parsing failures"""
    pass

def generate_dummy_value(field_schema: Dict[str, Any]) -> Any:
    """Generate a dummy value based on a JSON schema field definition."""
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
    """Generate dummy data from a JSON schema with error handling."""
    try:
        dummy_data = {}
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])
        for prop, prop_schema in properties.items():
            if prop in required_fields or not required_fields:
                value = generate_dummy_value(prop_schema)
                if value is not None:
                    dummy_data[prop] = value
        if schema.get("additionalProperties"):
            if isinstance(schema["additionalProperties"], dict):
                for _ in range(2):
                    prop_name = fake.word()
                    if prop_name not in dummy_data:
                        dummy_data[prop_name] = generate_dummy_value(schema["additionalProperties"])
        return dummy_data
    except Exception as e:
        logger.error(f"Error generating dummy data from schema: {str(e)}")
        return {}

def is_valid_flask_app(app_instance: Any) -> bool:
    """Check if an object is a valid Flask application instance."""
    if Flask is None:
        return False
    return isinstance(app_instance, Flask)

@lru_cache(maxsize=10)
def discover_flask_app(root_dir: str, app_var_name: str = "app") -> Tuple[Optional[Any], Optional[str]]:
    """
    Recursively scan for Flask apps in Python files with caching and parallel scanning.
    Returns a tuple of (app_instance, module_path) or (None, None) if not found.
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
            # Look for an attribute with the given name (commonly 'app')
            if hasattr(module, app_var_name):
                potential_app = getattr(module, app_var_name)
                if is_valid_flask_app(potential_app):
                    app_candidates.append((potential_app, file_path))
            # Also support factory functions named 'create_app'
            if hasattr(module, "create_app"):
                potential_app = getattr(module, "create_app")()
                if is_valid_flask_app(potential_app):
                    app_candidates.append((potential_app, file_path))
        except Exception as e:
            logger.debug(f"Skipping {file_path} due to error: {str(e)}")

    for curr_root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
        with concurrent.futures.ThreadPoolExecutor() as executor:
            python_files = [os.path.join(curr_root, f)
                            for f in files if f.endswith(".py") and not f.startswith('_')]
            executor.map(scan_file, python_files)
        if app_candidates:
            logger.info(f"Found Flask app in {app_candidates[0][1]}")
            return app_candidates[0]
    return None, None

class FlaskParser:
    """
    Parser for Flask APIs that:
    - Recursively discovers Flask apps only within your project directory.
    - Skips any dependencies or libraries outside the project root.
    - Extracts endpoint details from the Flask app's URL map, but only those
      whose view functions are defined in your project code.
    """

    def __init__(self, root_dir: Optional[str] = None, app_var_name: str = "app"):
        self.root_dir = os.path.abspath(root_dir) if root_dir else os.getcwd()
        self.app_var_name = app_var_name
        self.app: Optional[Flask] = None
        self.app_path: Optional[str] = None
        self.endpoints: List[Dict[str, Any]] = []
        self._discovery_cache: Dict[Tuple[str, str], Tuple[Flask, str]] = {}

    def discover_app(self, force_refresh: bool = False) -> bool:
        """Discover and load the Flask app from .py files under root_dir only."""
        cache_key = (self.root_dir, self.app_var_name)
        if not force_refresh and cache_key in self._discovery_cache:
            self.app, self.app_path = self._discovery_cache[cache_key]
            return True

        for dirpath, _, filenames in os.walk(self.root_dir):
            # skip virtualenv folders, __pycache__, etc.
            if any(ignored in dirpath for ignored in ("site-packages", "__pycache__", "venv", "env")):
                continue

            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                full_path = os.path.join(dirpath, fname)
                try:
                    app = self._load_app_from_file(full_path)
                    if app:
                        self.app, self.app_path = app, full_path
                        self._discovery_cache[cache_key] = (self.app, self.app_path)
                        return True
                except Exception as e:
                    logger.debug(f"Skipping {full_path}: {e}")
        raise FlaskDiscoveryError(
            f"No Flask app named '{self.app_var_name}' or create_app() found under {self.root_dir}"
        )

    def _load_app_from_file(self, filepath: str) -> Optional[Flask]:
        """Import a single .py file and return the Flask app instance if found."""
        module_name = os.path.splitext(os.path.relpath(filepath, self.root_dir))[0].replace(os.sep, ".")
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # direct app var
        if hasattr(module, self.app_var_name):
            obj = getattr(module, self.app_var_name)
            if isinstance(obj, Flask):
                return obj

        # factory
        if hasattr(module, "create_app") and callable(module.create_app):
            app = module.create_app()
            if isinstance(app, Flask):
                return app

        return None

    def parse_endpoints(self) -> List[Dict[str, Any]]:
        """Parse only project-defined endpoints from the Flask app's URL map."""
        if self.app is None:
            self.discover_app()

        endpoints: List[Dict[str, Any]] = []
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint == "static" or rule.rule.startswith("/static"):
                continue

            view_fn = self.app.view_functions.get(rule.endpoint)
            if not view_fn:
                continue

            # only include if the function's source file is under root_dir
            fn_path = getattr(view_fn, "__code__", None) and view_fn.__code__.co_filename
            if not fn_path or not os.path.abspath(fn_path).startswith(self.root_dir):
                continue

            methods = rule.methods.difference({"HEAD", "OPTIONS"})
            for method in methods:
                endpoints.append({
                    "method": method.upper(),
                    "path": rule.rule,
                    "description": f"Flask endpoint for '{rule.endpoint}'",
                    "operation_id": rule.endpoint,
                    "tags": [],
                    "headers": {"Content-Type": "application/json"},
                    "params": self._extract_path_params(rule.rule),
                    "body": {},
                    "deprecated": False,
                    "content_types": ["application/json"]
                })

        self.endpoints = endpoints
        return endpoints

    def _extract_path_params(self, rule: str) -> Dict[str, Any]:
        """
        Extract path parameters from Flask routing rules.
        E.g. '/user/<int:id>' → {"id": {..., "type":"integer"}}
        """
        params: Dict[str, Any] = {}
        pattern = re.compile(r"<(?:(\w+):)?([^>]+)>")
        for converter, name in pattern.findall(rule):
            type_map = {"int": "integer", "float": "number", "path": "string", "uuid": "string"}
            p_type = type_map.get(converter, "string")
            params[name] = {"in": "path", "type": p_type, "required": True, "description": ""}
        return params

class FlaskTester:
    """
    API tester for Flask that supports:
    - File upload
    - Form data handling
    - Cookie management
    - Retry mechanism and SSL handling.
    """
    def __init__(self, base_url: str = "", verify_ssl: bool = False, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.default_timeout = 30
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries

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
        Test an endpoint with comprehensive error handling and response processing.
        Returns a detailed response dictionary.
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

def get_flask_endpoints(
    root_dir: Optional[str] = None,
    app_var_name: str = "app",
    force_refresh: bool = False
) -> List[Dict[str, Any]]:
    """
    Public interface to get Flask endpoints.
    Discovers the Flask app, parses its routes, and caches the endpoint definitions.
    """
    try:
        parser = FlaskParser(root_dir, app_var_name)
        endpoints = parser.parse_endpoints()
        cache_file = os.path.join(root_dir or os.getcwd(), ".flask_parser_cache.json")
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

def test_flask_endpoint(
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
    Enhanced public interface for testing a Flask endpoint.
    Supports file uploads, cookies, and retry mechanism.
    """
    try:
        tester = FlaskTester(base_url, verify_ssl=verify_ssl, max_retries=max_retries)
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
