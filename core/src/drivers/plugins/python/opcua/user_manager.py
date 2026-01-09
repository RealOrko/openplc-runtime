"""
OPC UA User Manager.

This module provides authentication and user management for the OPC-UA server.
It supports password authentication, certificate authentication, and anonymous access.
"""

import base64
import hashlib
import os
import sys
from types import SimpleNamespace
from typing import Dict, Optional, Any

from asyncua.server.user_managers import UserManager, UserRole

# Import bcrypt with fallback
try:
    import bcrypt
    _bcrypt_available = True
except ImportError:
    _bcrypt_available = False

# Add directories to path for module access
_current_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_current_dir)
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

# Import logging (handle both package and direct loading)
try:
    from .opcua_logging import log_info, log_warn, log_error
except ImportError:
    from opcua_logging import log_info, log_warn, log_error

from shared.plugin_config_decode.opcua_config_model import OpcuaConfig


class OpenPLCUserManager(UserManager):
    """
    Custom user manager for OpenPLC authentication.

    Supports:
    - Password authentication (bcrypt hashed)
    - Certificate authentication (fingerprint matching)
    - Anonymous access

    Maps OpenPLC roles to asyncua UserRole enum:
    - viewer -> UserRole.User (read-only)
    - operator -> UserRole.User (read/write via callbacks)
    - engineer -> UserRole.Admin (full access)
    """

    # Map OpenPLC roles to asyncua UserRole enum
    ROLE_MAPPING = {
        "viewer": UserRole.User,      # Read-only access
        "operator": UserRole.User,    # Read/write access (controlled by callbacks)
        "engineer": UserRole.Admin    # Full access
    }

    def __init__(self, config: OpcuaConfig):
        """
        Initialize the user manager.

        Args:
            config: OpcuaConfig instance with users and security profiles
        """
        super().__init__()
        self.config = config

        # Build user dictionaries
        self.users = {
            user.username: user
            for user in config.users
            if user.type == "password"
        }
        self.cert_users = {
            user.certificate_id: user
            for user in config.users
            if user.type == "certificate"
        }

        # Build security policy URI mapping
        self._policy_uri_mapping = self._build_policy_uri_mapping()

        log_info(f"UserManager initialized: {len(self.users)} password users, "
                 f"{len(self.cert_users)} certificate users")

    def get_user(
        self,
        isession,
        username: Optional[str] = None,
        password: Optional[str] = None,
        certificate: Optional[Any] = None
    ) -> Optional[Any]:
        """
        Authenticate user with security profile enforcement.

        Args:
            isession: The internal session object
            username: Username for password authentication
            password: Password for password authentication
            certificate: Certificate for certificate authentication

        Returns:
            User object with role attribute, or None if authentication fails
        """
        # Detect authentication method first
        auth_method = self._detect_auth_method(username, password, certificate)
        log_info(f"Authentication attempt detected: method={auth_method}")

        # Try to resolve the profile normally
        profile = self._get_profile_for_session(isession)

        # FALLBACK: if cannot resolve profile, try to find one that supports the auth method
        if not profile:
            policy_uri = getattr(isession, 'security_policy_uri', None)
            log_warn(
                f"No security profile mapped for session (policy_uri={policy_uri}). "
                f"Attempting fallback using auth method: {auth_method}"
            )

            # Try to find a profile that supports this authentication method
            profile = self._find_profile_by_auth_method(auth_method)

            if profile:
                log_info(f"Using fallback security profile: '{profile.name}' (supports {auth_method})")
            else:
                log_error(
                    f"No security profile found that supports authentication method '{auth_method}'. "
                    f"Session policy URI: {policy_uri}"
                )
                return None

        # Validate that the profile supports the authentication method
        if auth_method not in profile.auth_methods:
            log_error(
                f"Authentication method '{auth_method}' not allowed for security profile "
                f"'{profile.name}'. Allowed methods: {profile.auth_methods}"
            )
            return None

        # Authenticate based on method
        user = None

        if auth_method == "Username" and username and password:
            user = self._authenticate_password(username, password)

        elif auth_method == "Certificate" and certificate:
            user = self._authenticate_certificate(certificate)

        elif auth_method == "Anonymous":
            user = self._authenticate_anonymous(profile)

        if user:
            log_info(
                f"User '{getattr(user, 'username', 'anonymous')}' authenticated successfully "
                f"using '{auth_method}' method for profile '{profile.name}'"
            )
            return user
        else:
            log_warn(
                f"Authentication failed for method '{auth_method}' on profile '{profile.name}'"
            )
            return None

    def _authenticate_password(self, username: str, password: str) -> Optional[Any]:
        """
        Authenticate using username and password.

        Args:
            username: The username
            password: The password

        Returns:
            User object or None
        """
        if username not in self.users:
            log_warn(f"User '{username}' not found in configuration")
            return None

        user = self.users[username]
        if not self._validate_password(password, user.password_hash):
            log_warn(f"Password validation failed for user '{username}'")
            return None

        # Add asyncua-compatible role and preserve OpenPLC role
        user.openplc_role = str(user.role)  # Ensure it's a string
        user.role = self.ROLE_MAPPING.get(user.openplc_role, UserRole.User)
        return user

    def _authenticate_certificate(self, certificate: Any) -> Optional[Any]:
        """
        Authenticate using certificate.

        Args:
            certificate: The client certificate

        Returns:
            User object or None
        """
        cert_id = self._extract_cert_id(certificate)
        if not cert_id or cert_id not in self.cert_users:
            log_warn(f"Certificate not found in trusted certificates (cert_id={cert_id})")
            return None

        user = self.cert_users[cert_id]
        # Add asyncua-compatible role and preserve OpenPLC role
        user.openplc_role = str(user.role)  # Ensure it's a string
        user.role = self.ROLE_MAPPING.get(user.openplc_role, UserRole.User)
        log_info(f"Certificate authenticated as user with role '{user.openplc_role}'")
        return user

    def _authenticate_anonymous(self, profile: Any) -> Optional[Any]:
        """
        Authenticate as anonymous user.

        Args:
            profile: The security profile

        Returns:
            Anonymous user object or None
        """
        if "Anonymous" not in profile.auth_methods:
            log_warn("Anonymous authentication not allowed for this profile")
            return None

        user = SimpleNamespace()
        user.username = "anonymous"
        user.openplc_role = "viewer"
        user.role = UserRole.User  # Map to asyncua UserRole enum
        return user

    def _extract_cert_id(self, certificate: Any) -> Optional[str]:
        """
        Extract certificate ID using fingerprint matching.

        Args:
            certificate: The client certificate

        Returns:
            Certificate ID if found in trusted list, None otherwise
        """
        try:
            # Convert session certificate to fingerprint
            client_fingerprint = self._cert_to_fingerprint(certificate)
            if not client_fingerprint:
                return None

            # Compare with configured certificate fingerprints
            for cert_info in self.config.security.trusted_client_certificates:
                config_fingerprint = self._pem_to_fingerprint(cert_info["pem"])
                if config_fingerprint and client_fingerprint == config_fingerprint:
                    log_info(f"Certificate matched: {cert_info['id']} "
                             f"(fingerprint: {client_fingerprint[:16]}...)")
                    return cert_info["id"]

            log_warn(f"Certificate not found in trusted list "
                     f"(fingerprint: {client_fingerprint[:16]}...)")
        except Exception as e:
            log_error(f"Certificate fingerprint extraction failed: {e}")

        return None

    def _build_policy_uri_mapping(self) -> Dict[str, str]:
        """
        Build mapping from OPC-UA security policy URIs to profile names.

        Returns:
            Dict mapping policy URI to profile name
        """
        uri_mapping = {}

        for profile in self.config.server.security_profiles:
            if not profile.enabled:
                continue

            # Map config policy+mode to standard OPC-UA URI
            policy_uri = self._get_standard_policy_uri(
                profile.security_policy,
                profile.security_mode
            )
            if policy_uri:
                uri_mapping[policy_uri] = profile.name

        log_info(f"Built security policy URI mapping: {uri_mapping}")
        return uri_mapping

    def _get_standard_policy_uri(
        self,
        security_policy: str,
        security_mode: str
    ) -> Optional[str]:
        """
        Get standard OPC-UA security policy URI for config values.

        Args:
            security_policy: Policy name from config
            security_mode: Mode name from config

        Returns:
            Standard OPC-UA policy URI or None
        """
        if security_policy == "None" and security_mode == "None":
            return "http://opcfoundation.org/UA/SecurityPolicy#None"
        elif security_policy == "Basic256Sha256":
            return "http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256"
        elif security_policy == "Aes128_Sha256_RsaOaep":
            return "http://opcfoundation.org/UA/SecurityPolicy#Aes128_Sha256_RsaOaep"
        elif security_policy == "Aes256_Sha256_RsaPss":
            return "http://opcfoundation.org/UA/SecurityPolicy#Aes256_Sha256_RsaPss"
        else:
            log_warn(f"Unknown security policy: {security_policy}")
            return None

    def _get_profile_for_session(self, isession) -> Optional[Any]:
        """
        Get security profile for the session based on its security policy URI.

        Args:
            isession: The internal session object

        Returns:
            Security profile object or None
        """
        try:
            policy_uri = getattr(isession, 'security_policy_uri', None)
            if not policy_uri:
                log_warn("Session has no security_policy_uri attribute")
                return None

            profile_name = self._policy_uri_mapping.get(policy_uri)
            if not profile_name:
                log_warn(f"No profile mapping found for policy URI: {policy_uri}")
                return None

            # Find the profile object
            for profile in self.config.server.security_profiles:
                if profile.name == profile_name and profile.enabled:
                    return profile

            log_error(f"Profile '{profile_name}' not found or disabled in configuration")
            return None
        except Exception as e:
            log_error(f"Failed to resolve security profile for session: {e}")
            return None

    def _cert_to_fingerprint(self, certificate: Any) -> Optional[str]:
        """
        Convert certificate object to SHA256 fingerprint.

        Args:
            certificate: Certificate object (various formats supported)

        Returns:
            Fingerprint string (colon-separated hex) or None
        """
        try:
            if hasattr(certificate, 'der'):
                # Certificate object with der attribute
                cert_der = certificate.der
            elif hasattr(certificate, 'data'):
                # Certificate object with data attribute
                cert_der = certificate.data
            elif isinstance(certificate, bytes):
                # Raw certificate data
                cert_der = certificate
            else:
                # Try to convert to string and then decode
                cert_str = str(certificate)
                if "-----BEGIN CERTIFICATE-----" in cert_str:
                    # PEM format - extract base64 content
                    cert_lines = cert_str.split('\n')
                    cert_b64 = ''.join([
                        line for line in cert_lines
                        if not line.startswith('-----')
                    ])
                    cert_der = base64.b64decode(cert_b64)
                else:
                    log_warn(f"Unknown certificate format: {type(certificate)}")
                    return None

            # Calculate SHA256 fingerprint
            fingerprint = hashlib.sha256(cert_der).hexdigest().upper()
            return ':'.join(fingerprint[i:i+2] for i in range(0, len(fingerprint), 2))
        except Exception as e:
            log_error(f"Failed to extract certificate fingerprint: {e}")
            return None

    def _pem_to_fingerprint(self, pem_str: str) -> Optional[str]:
        """
        Convert PEM certificate string to SHA256 fingerprint.

        Args:
            pem_str: PEM-encoded certificate string

        Returns:
            Fingerprint string (colon-separated hex) or None
        """
        try:
            # Extract base64 content from PEM
            pem_lines = pem_str.strip().split('\n')
            cert_b64 = ''.join([
                line for line in pem_lines
                if not line.startswith('-----')
            ])
            cert_der = base64.b64decode(cert_b64)

            # Calculate SHA256 fingerprint
            fingerprint = hashlib.sha256(cert_der).hexdigest().upper()
            return ':'.join(fingerprint[i:i+2] for i in range(0, len(fingerprint), 2))
        except Exception as e:
            log_error(f"Failed to convert PEM to fingerprint: {e}")
            return None

    def _detect_auth_method(
        self,
        username: Optional[str],
        password: Optional[str],
        certificate: Optional[Any]
    ) -> str:
        """
        Detect which authentication method is being used.

        Args:
            username: Username if provided
            password: Password if provided
            certificate: Certificate if provided

        Returns:
            Authentication method: "Certificate", "Username", or "Anonymous"
        """
        if certificate:
            return "Certificate"
        elif username and password:
            return "Username"
        else:
            return "Anonymous"

    def _find_profile_by_auth_method(self, auth_method: str) -> Optional[Any]:
        """
        Find a security profile that supports the given authentication method.

        Args:
            auth_method: The authentication method to find

        Returns:
            Security profile object or None
        """
        for profile in self.config.server.security_profiles:
            if not profile.enabled:
                continue
            if auth_method in profile.auth_methods:
                log_info(f"Found profile '{profile.name}' supporting {auth_method}")
                return profile

        log_warn(f"No enabled profile found supporting authentication method: {auth_method}")
        return None

    def _validate_password(self, password: str, password_hash: str) -> bool:
        """
        Validate password against hash using bcrypt or fallback.

        Args:
            password: Plain text password
            password_hash: Bcrypt hash

        Returns:
            True if password matches
        """
        if _bcrypt_available:
            try:
                return bcrypt.checkpw(password.encode(), password_hash.encode())
            except Exception as e:
                log_error(f"bcrypt validation error: {e}")
                return False
        else:
            # Fail securely - bcrypt is required for password authentication
            log_error("bcrypt not available - password authentication disabled for security")
            return False
