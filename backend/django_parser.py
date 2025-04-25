import os
import sys
import json
import logging
import time
import re
import concurrent.futures
from functools import lru_cache
from typing import List, Dict, Any, Optional, Tuple

import requests
from faker import Faker

# Configure logging for production-grade visibility.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("DjangoParser")

fake = Faker()

# Ensure Django is available and configured.
try:
    import django
    from django.conf import settings
    from django.urls import get_resolver, URLPattern, URLResolver
except ImportError:
    django = None
    logger.warning("Django is not installed. Django functionality will be disabled.")

if django and not settings.configured:
    # Only attempt to set up Django if a proper settings module is defined.
    django_settings_module = os.environ.get('DJANGO_SETTINGS_MODULE')
    if not django_settings_module:
        logger.warning("DJANGO_SETTINGS_MODULE is not set. Django functionality may be limited.")
    else:
        try:
            django.setup()
            logger.info("Django setup completed successfully.")
        except Exception as e:
            logger.warning(f"Django setup error: {e}. Django functionality may be limited.")

def generate_dummy_value(field_schema: Dict[str, Any]) -> Any:
    """
    Generate a dummy value based on a JSON schema field definition.
    This function supports various types like string, integer, number, boolean, array, and object.
    """
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
                # Produce a text string that meets the length requirements.
                text = fake.text(max_nb_chars=max_len).strip()
                return text[:max_len].ljust(min_len)
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
                # Generate unique items until at least min_items are available.
                while len(items) < min_items:
                    items.add(generate_dummy_value(item_schema))
                return list(items)[:max_items]
            else:
                count = fake.random_int(min=min_items, max=max_items)
                return [generate_dummy_value(item_schema) for _ in range(count)]
        elif field_type == "object":
            return generate_dummy_data_from_schema(field_schema)
    except Exception as e:
        logger.warning(f"Failed to generate dummy value for schema {field_schema}: {e}")
        return None

def generate_dummy_data_from_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate dummy data from a JSON schema with robust error handling.
    Iterates over properties and uses `generate_dummy_value` to create values.
    """
    try:
        dummy_data = {}
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])
        for prop, prop_schema in properties.items():
            # If no required fields are specified, use all properties.
            if prop in required_fields or not required_fields:
                value = generate_dummy_value(prop_schema)
                if value is not None:
                    dummy_data[prop] = value
        # Support additional properties if defined in the schema.
        additional = schema.get("additionalProperties")
        if additional and isinstance(additional, dict):
            for _ in range(2):
                prop_name = fake.word()
                if prop_name not in dummy_data:
                    dummy_data[prop_name] = generate_dummy_value(additional)
        return dummy_data
    except Exception as e:
        logger.error(f"Error generating dummy data from schema: {e}")
        return {}

class DjangoParser:
    """
    Parser for Django APIs that:
    - Ensures Django settings are loaded.
    - Recursively extracts URL patterns from the Django URL configuration.
    - Additionally, scans for .py files in the user project directory to detect API endpoints.
    - Returns a list of endpoint dictionaries for UI consumption.
    
    **Note:** This parser relies on Django's get_resolver() for registered URLs and a file scan for
    endpoints in user code (excluding environment or installed/system directories).
    """
    def __init__(self, root_dir: Optional[str] = None):
        self.root_dir = os.path.abspath(root_dir) if root_dir else os.getcwd()
        self.endpoints: List[Dict[str, Any]] = []

    def parse_endpoints(self) -> List[Dict[str, Any]]:
        """
        Parse endpoints from both Django's URL resolver and file scanning.
        Each endpoint includes its path and allowed HTTP methods.
        """
        endpoints: List[Dict[str, Any]] = []

        # 1) Use Django's URL resolver to get all registered URLPatterns/Resolvers
        try:
            resolver = get_resolver()
            pattern_list = self._get_patterns(resolver.url_patterns)
            for path, callback in pattern_list:
                # determine allowed methods
                methods = ["GET"]
                try:
                    if hasattr(callback, "view_class"):
                        methods = list(callback.view_class.http_method_names)
                    elif hasattr(callback, "actions"):  # DRF ViewSet actions
                        methods = [m.upper() for m in callback.actions.keys()]
                    elif hasattr(callback, "allowed_methods"):
                        methods = list(callback.allowed_methods)
                    methods = [m.upper() for m in methods]
                except Exception:
                    methods = ["GET"]

                endpoints.append({
                    "methods": methods,
                    "path": path,
                    "description": f"Django endpoint for '{path}'",
                    "operation_id": path,
                    "tags": [],
                    "headers": {"Content-Type": "application/json"},
                    "params": self._extract_path_params(path),
                    "body": {},
                    "deprecated": False,
                    "content_types": ["application/json"]
                })
        except Exception as e:
            logger.error(f"Error using Django resolver: {e}")

        # 2) Scan .py files in project for explicit path/re_path calls
        scanned = self._scan_for_file_endpoints()
        # merge, avoiding duplicates by path+methods
        existing = {(e["path"], tuple(e["methods"])) for e in endpoints}
        for fe in scanned:
            key = (fe["path"], tuple(fe["methods"]))
            if key not in existing:
                endpoints.append(fe)

        self.endpoints = endpoints
        return endpoints

    def _get_patterns(self, urlpatterns: List[Any], base: str = "") -> List[Tuple[str, Any]]:
        """
        Recursively traverse URL patterns and return a flattened list of tuples:
        (URL string, view callback).
        """
        results: List[Tuple[str, Any]] = []
        for pattern in urlpatterns:
            try:
                # extract raw pattern text
                raw = getattr(pattern.pattern, "_route", None)
                if raw is None:
                    raw = pattern.pattern.regex.pattern
                url = base + self._convert_django_regex(raw)
                if isinstance(pattern, URLPattern):
                    results.append((url, pattern.callback))
                elif isinstance(pattern, URLResolver):
                    # dive into nested
                    results.extend(self._get_patterns(pattern.url_patterns, url))
            except Exception as e:
                logger.warning(f"Error processing pattern {pattern}: {e}")
        return results

    def _convert_django_regex(self, regex: str) -> str:
        """
        Simplify a Django regex or route pattern to a URL path.
        '^api/items/(?P<id>[0-9]+)/$' or 'api/items/<int:id>/' → '/api/items/<id>/'
        """
        # strip anchors
        url = regex.strip("^$")
        # named groups
        url = re.sub(r"\(\?P<([^>]+)>[^\)]+\)", r"<\1>", url)
        # unnamed groups
        url = re.sub(r"\([^\)]+\)", r"<param>", url)
        if not url.startswith("/"):
            url = "/" + url
        return url

    def _extract_path_params(self, url: str) -> Dict[str, Any]:
        """
        Extract path parameters from the URL.
        '/api/items/<id>/' → {"id": {...}}
        """
        params: Dict[str, Any] = {}
        for match in re.findall(r"<([^>]+)>", url):
            name = match.split(":", 1)[-1]  # handle converter syntax
            params[name] = {"in": "path", "type": "string", "required": True, "description": ""}
        return params

    def _scan_for_file_endpoints(self) -> List[Dict[str, Any]]:
        """
        Recursively scan for .py files in the project directory (excluding env dirs)
        and extract endpoint URL definitions using regex on path()/re_path().
        """
        endpoints: List[Dict[str, Any]] = []
        skip = {"env", "venv", "__pycache__", "site-packages"}
        for dirpath, dirnames, filenames in os.walk(self.root_dir):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            for file in filenames:
                if not file.endswith(".py"):
                    continue
                fp = os.path.join(dirpath, file)
                try:
                    content = open(fp, "r", encoding="utf-8").read()
                except Exception:
                    continue

                # find path(...) and re_path(...)
                for m in re.findall(r"(?:path|re_path)\(\s*['\"]([^'\"]+)['\"]", content):
                    url = m.strip()
                    if not url.startswith("/"):
                        url = "/" + url
                    endpoints.append({
                        "methods": ["GET"],
                        "path": url,
                        "description": f"File-scanned endpoint from {file}: {url}",
                        "operation_id": url,
                        "tags": [],
                        "headers": {"Content-Type": "application/json"},
                        "params": self._extract_path_params(url),
                        "body": {},
                        "deprecated": False,
                        "content_types": ["application/json"]
                    })
        return endpoints


class DjangoTester:
    """
    API tester for Django that supports:
    - File uploads and form data.
    - Cookie management.
    - Retry mechanism and SSL verification.
    
    This class is built on top of the requests.Session for efficient HTTP testing.
    """
    def __init__(self, base_url: str = "", verify_ssl: bool = False, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.default_timeout = 30  # seconds
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
        Test an endpoint using HTTP requests. Retries on failure as per max_retries.
        """
        try:
            method = endpoint["methods"][0].lower()  # Use the first supported method for the test.
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
                    break  # Exit loop if request succeeds.
                except requests.exceptions.RequestException as e:
                    if attempt == self.max_retries:
                        raise
                    logger.warning(f"Request failed on attempt {attempt + 1}: {e}. Retrying...")
                    time.sleep(1)

            return self._process_response(response)
        except Exception as e:
            logger.error(f"Error during endpoint test: {e}")
            return {
                "error": str(e),
                "status_code": None,
                "success": False,
                "response_time": None
            }

    def _build_url(self, path: str, environment: Optional[Dict[str, Any]]) -> str:
        """
        Build the full URL for an endpoint, supporting environment-based substitutions.
        """
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
        """
        Replace placeholder variables in headers with values from environment.
        """
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
        """
        Process URL parameters, generating dummy values if examples are not provided.
        """
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
        """
        Process the HTTP response and extract key information including headers, cookies, and body.
        """
        try:
            json_data = response.json()
        except ValueError:
            json_data = None
        return {
            "status_code": response.status_code,
            "success": 200 <= response.status_code < 300,
            "response_time": response.elapsed.total_seconds() * 1000,  # in milliseconds
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

def get_django_endpoints(
    root_dir: Optional[str] = None,
    force_refresh: bool = False
) -> List[Dict[str, Any]]:
    """
    Public interface to retrieve Django endpoints.
    Extracts URLs via Django's get_resolver() and file scanning, then caches the result to a file.
    """
    try:
        parser = DjangoParser(root_dir)
        endpoints = parser.parse_endpoints()
        cache_file = os.path.join(root_dir or os.getcwd(), ".django_parser_cache.json")
        if not force_refresh and os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as f:
                    cached_endpoints = json.load(f)
                if cached_endpoints:
                    return cached_endpoints
            except Exception as e:
                logger.warning(f"Failed to load cache file: {e}")
        with open(cache_file, "w") as f:
            json.dump(endpoints, f)
        return endpoints
    except Exception as e:
        logger.error(f"Endpoint parsing failed: {e}")
        return []

def test_django_endpoint(
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
    Public interface for testing a Django endpoint.
    Wraps DjangoTester to support file uploads, cookies, and retries.
    """
    try:
        tester = DjangoTester(base_url, verify_ssl=verify_ssl, max_retries=max_retries)
        return tester.test_endpoint(
            endpoint=endpoint,
            environment=environment,
            auth=auth,
            files=files,
            cookies=cookies
        )
    except Exception as e:
        logger.error(f"Endpoint testing failed: {e}")
        return {
            "error": str(e),
            "status_code": None,
            "success": False,
            "response_time": None
        }
