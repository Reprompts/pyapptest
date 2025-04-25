import sys
import os
import json
import requests
from datetime import datetime
from typing import List, Dict, Any
import streamlit as st
from backend.flask_parser import get_flask_endpoints  # Import Flask endpoint parser
from backend.django_parser import get_django_endpoints, test_django_endpoint
from backend.fastapi_parser import get_fastapi_endpoints

# Add the project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Set page config
st.set_page_config(
    page_title="pyapitest – Advanced API Tester",
    layout="wide",
    page_icon="🔧",
    initial_sidebar_state="expanded"
)

# Session state initialization
if 'endpoints' not in st.session_state:
    st.session_state.endpoints = []
if 'requests_history' not in st.session_state:
    st.session_state.requests_history = []
if 'environments' not in st.session_state:
    # Default "Local" URL will be updated based on the selected framework
    st.session_state.environments = {
        "Local": "http://localhost:8000",
        "Production": "https://api.example.com"  # Default production URL
    }
if 'current_env' not in st.session_state:
    st.session_state.current_env = "Local"
if 'collections' not in st.session_state:
    st.session_state.collections = {}
if 'active_tab' not in st.session_state:
    st.session_state.active_tab = "Endpoints"
if 'production_url' not in st.session_state:
    st.session_state.production_url = "https://api.example.com"  # Separate production URL control

def save_to_history(request_data: Dict[str, Any]) -> None:
    """Save request to history with size limit."""
    st.session_state.requests_history.append(request_data)
    if len(st.session_state.requests_history) > 50:
        st.session_state.requests_history.pop(0)

def send_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    params: Dict[str, str],
    body: Any,
    body_type: str
) -> Dict[str, Any]:
    """Send HTTP request with enhanced error handling."""
    try:
        start_time = datetime.now()
        verify_ssl = st.session_state.current_env != "Local"  # Verify SSL for production
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=body if body_type == "JSON" else None,
            data=body if body_type != "JSON" else None,
            verify=verify_ssl
        )
        duration = (datetime.now() - start_time).total_seconds() * 1000
        return {
            "method": method,
            "url": url,
            "status_code": response.status_code,
            "response_time": f"{duration:.2f}ms",
            "response_headers": dict(response.headers),
            "response_body": response.text,
            "timestamp": datetime.now().isoformat(),
            "request_headers": headers,
            "request_params": params,
            "request_body": body,
            "environment": st.session_state.current_env
        }
    except Exception as e:
        st.error(f"Error making request: {str(e)}")
        return None

def get_generic_endpoints(framework: str) -> List[Dict[str, Any]]:
    """Fallback endpoint loader for non-FastAPI frameworks."""
    base_url = st.session_state.environments.get(st.session_state.current_env, "http://localhost:8000")
    endpoints = [{
        "method": "GET",
        "path": "/users",
        "headers": {"Content-Type": "application/json"},
        "params": {},
        "body": {},
        "description": "Get all users"
    }]
    
    # Framework-specific endpoint UI adjustments
    if framework == "Django":
        endpoints[0]["path"] = "/api/users"
        endpoints[0]["description"] = "Get all users (Django)"
    elif framework == "Other":
        endpoints[0]["path"] = "/endpoint"
        endpoints[0]["description"] = "Sample endpoint"
        
    return endpoints

def render_endpoint_form(idx: int, endpoint: Dict[str, Any]) -> None:
    """Render an editable endpoint form with production URL support."""
    with st.form(key=f"endpoint_form_{idx}"):
        st.markdown(f"### {endpoint.get('description', f'Endpoint {idx+1}')}")
        
        # Method and URL configuration
        col_method, col_path = st.columns([1, 3])
        with col_method:
            # Provide all common HTTP methods regardless of endpoint configuration.
            allowed_methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
            # Use the endpoint 'method' if available, otherwise default to GET.
            default_method = endpoint.get("method", "GET").upper()
            if default_method not in allowed_methods:
                default_method = "GET"
            method = st.selectbox(
                "Method",
                allowed_methods,
                index=allowed_methods.index(default_method),
                key=f"method_{idx}"
            )
        with col_path:
            # Determine base URL depending on environment
            base_url = (st.session_state.production_url 
                        if st.session_state.current_env == "Production"
                        else st.session_state.environments.get(st.session_state.current_env, ""))
            path = st.text_input("Path", value=endpoint.get("path", ""), key=f"path_{idx}")
            full_url = base_url.rstrip("/") + "/" + path.lstrip("/")
            st.caption(f"Full URL: {full_url}")

        # Request components
        with st.expander("Headers"):
            headers = st.text_area(
                "Headers (JSON format)",
                value=json.dumps(endpoint.get("headers", {}), indent=2),
                key=f"headers_{idx}",
                height=150
            )
        with st.expander("Query Parameters"):
            params = st.text_area(
                "Params (JSON format)",
                value=json.dumps(endpoint.get("params", {}), indent=2),
                key=f"params_{idx}",
                height=150
            )
        with st.expander("Body"):
            body_type = st.radio(
                "Body Type", 
                ["JSON", "Text", "Form Data", "Binary"],
                key=f"body_type_{idx}", 
                horizontal=True
            )
            if body_type == "JSON":
                body = st.text_area(
                    "Body (JSON)", 
                    value=json.dumps(endpoint.get("body", {}), indent=2),
                    key=f"body_{idx}", 
                    height=200
                )
            elif body_type == "Text":
                body = st.text_area(
                    "Body (Text)", 
                    value="",
                    key=f"body_{idx}", 
                    height=200
                )
            else:
                st.warning(f"{body_type} support coming soon!")
                body = ""
        
        # Form submission
        submitted = st.form_submit_button("Send Request")
        if submitted:
            try:
                headers_dict = json.loads(headers) if headers else {}
                params_dict = json.loads(params) if params else {}
                body_data = json.loads(body) if body and body_type == "JSON" else body
                
                # Determine base URL
                base_url = (st.session_state.production_url 
                            if st.session_state.current_env == "Production"
                            else st.session_state.environments.get(st.session_state.current_env, ""))
                
                response_data = send_request(
                    method=method,
                    url=base_url.rstrip("/") + "/" + path.lstrip("/"),
                    headers=headers_dict,
                    params=params_dict,
                    body=body_data,
                    body_type=body_type
                )
                
                if response_data:
                    save_to_history(response_data)
                    st.session_state.last_response = response_data
                    st.success("Request sent successfully!")
                    
                    # Display response
                    with st.expander("View Response", expanded=True):
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Status Code", response_data["status_code"])
                        col2.metric("Response Time", response_data["response_time"])
                        col3.metric("Size", f"{len(response_data['response_body'])} bytes")
                        
                        res_tab1, res_tab2 = st.tabs(["Body", "Headers"])
                        with res_tab1:
                            try:
                                json_response = json.loads(response_data["response_body"])
                                st.json(json_response)
                            except ValueError:
                                st.text(response_data["response_body"])
                        with res_tab2:
                            st.json(response_data["response_headers"])
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {str(e)}")

def main():
    st.title("🔧 pyapitest – Advanced API Tester")
    st.caption("A comprehensive API testing tool with automatic endpoint discovery")
    
    # Sidebar Configuration
    with st.sidebar:
        st.title("Configuration")
        
        # Framework selection
        framework = st.selectbox(
            "Select Framework", 
            ["FastAPI", "Flask", "Django", "Other"],
            help="Select your API framework for endpoint detection"
        )
        # Update Local environment based on the selected framework
        if framework == "FastAPI":
            st.session_state.environments["Local"] = "http://localhost:8000"
        elif framework == "Flask":
            st.session_state.environments["Local"] = "http://localhost:5000"
        elif framework == "Django":
            # For Django, update Local URL if needed (e.g., Django might run on 8001)
            st.session_state.environments["Local"] = "http://localhost:8001"
        
        # Environment management
        st.subheader("Environment Configuration")
        current_env = st.selectbox(
            "Current Environment", 
            options=list(st.session_state.environments.keys()),
            key="current_env"
        )
        # Production URL (only shown when Production is selected)
        if st.session_state.current_env == "Production":
            st.session_state.production_url = st.text_input(
                "Production API Base URL",
                value=st.session_state.production_url,
                help="Enter the base URL for your production API"
            )
        
        with st.expander("Manage Environments"):
            env_col1, env_col2 = st.columns(2)
            with env_col1:
                new_env_name = st.text_input("New Environment Name")
            with env_col2:
                new_env_url = st.text_input("Base URL")
            if st.button("Add Environment"):
                if new_env_name and new_env_url:
                    st.session_state.environments[new_env_name] = new_env_url
                    st.success(f"Environment '{new_env_name}' added!")
                else:
                    st.warning("Please provide both name and URL")
        
        # Endpoint management
        st.subheader("Endpoint Management")
        if st.button("Reload Endpoints"):
            try:
                if framework == "FastAPI":
                    st.session_state.endpoints = get_fastapi_endpoints()
                    st.success(f"Loaded {len(st.session_state.endpoints)} FastAPI endpoints!")
                elif framework == "Flask":
                    st.session_state.endpoints = get_flask_endpoints()  # Use Flask parser
                    st.success(f"Loaded {len(st.session_state.endpoints)} Flask endpoints!")
                elif framework == "Django":
                    # Updated Django branch: now using the proper django endpoint parser
                    st.session_state.endpoints = get_django_endpoints()
                    st.success(f"Loaded {len(st.session_state.endpoints)} Django endpoints!")
                else:
                    st.session_state.endpoints = get_generic_endpoints(framework)
                    st.success(f"Loaded {len(st.session_state.endpoints)} endpoints for {framework}")
            except Exception as e:
                st.error(f"Error loading endpoints: {str(e)}")
        
        if st.button("Add Empty Endpoint"):
            st.session_state.endpoints.append({
                "method": "GET",
                "path": "/new-endpoint",
                "headers": {},
                "params": {},
                "body": {},
                "description": "New Endpoint"
            })
            st.rerun()
        
        # Navigation
        st.subheader("Navigation")
        st.radio(
            "View", 
            ["Endpoints", "History", "Collections"],
            key="active_tab", 
            label_visibility="collapsed"
        )
    
    # Main Content Area
    if st.session_state.active_tab == "Endpoints":
        st.header("API Endpoints")
        st.write(f"Showing endpoints for {framework}. Edit details as needed.")
        
        # Environment indicator
        env_badge = st.empty()
        with env_badge:
            if st.session_state.current_env == "Production":
                st.warning(f"Testing against PRODUCTION environment: {st.session_state.production_url}")
            else:
                st.info(f"Testing against {st.session_state.current_env} environment")
        
        for idx, endpoint in enumerate(st.session_state.endpoints):
            with st.container():
                render_endpoint_form(idx, endpoint)
                if st.button(f"Delete Endpoint {idx+1}", key=f"delete_endpoint_{idx}"):
                    st.session_state.endpoints.pop(idx)
                    st.rerun()
                st.markdown("---")
    
    elif st.session_state.active_tab == "History":
        st.header("Request History")
        if st.session_state.requests_history:
            for i, req in enumerate(reversed(st.session_state.requests_history)):
                env_tag = "🟢" if req.get("environment") != "Production" else "🔴"
                with st.expander(f"{env_tag} {req['method']} {req['url']} - {req['status_code']}"):
                    cols = st.columns(3)
                    cols[0].write(f"**Status:** {req['status_code']}")
                    cols[1].write(f"**Time:** {req['response_time']}")
                    cols[2].write(f"**Size:** {len(req['response_body'])} bytes")
                    
                    if st.button(f"Replay Request {i+1}", key=f"replay_{i}"):
                        st.session_state.endpoints.append({
                            "method": req["method"],
                            "path": req["url"].replace(
                                st.session_state.environments.get(req.get("environment", ""), ""),
                                ""
                            ),
                            "headers": req["request_headers"],
                            "params": req["request_params"],
                            "body": req["request_body"],
                            "description": f"Replayed: {req['method']} {req['url']}"
                        })
                        st.session_state.active_tab = "Endpoints"
                        st.rerun()
                    
                    try:
                        st.json(json.loads(req["response_body"]))
                    except Exception:
                        st.text(req["response_body"])
        else:
            st.info("No requests in history")
    
    elif st.session_state.active_tab == "Collections":
        st.header("API Collections")
        st.write("Organize endpoints into collections for easier testing.")
        
        col_name = st.text_input("New Collection Name", key="new_collection_name")
        if st.button("Create Collection"):
            if col_name:
                st.session_state.collections[col_name] = []
                st.success(f"Collection '{col_name}' created!")
            else:
                st.warning("Please enter a collection name")
        
        for col_name, endpoints in st.session_state.collections.items():
            with st.expander(col_name):
                if endpoints:
                    for endpoint in endpoints:
                        st.write(f"{endpoint['method']} {endpoint['path']}")
                else:
                    st.info("No endpoints in this collection")

if __name__ == "__main__":
    main()
