"""URL reachability checker for BibTeX entries"""

import logging
import requests
from typing import Tuple
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


def is_doi_url(url: str) -> bool:
    """
    Check if URL is a DOI resolver URL (skip reachability checks for these).
    
    DOI URLs like https://doi.org/10.xxxx/... or https://dx.doi.org/... are
    handled via the 'doi' field normalization and don't need separate reachability checks.
    
    Args:
        url: URL string
    
    Returns:
        True if URL is a DOI resolver, False otherwise
    """
    if not url:
        return False
    
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        # Match doi.org, www.doi.org, dx.doi.org, etc.
        return "doi.org" in netloc
    except Exception:
        return False


def classify_request_exception(e: Exception) -> str:
    """
    Classify a requests exception into a short string for reporting.
    
    Args:
        e: Exception from requests
    
    Returns:
        Classification string: "timeout", "dns", "ssl", "connection_error", etc.
    """
    exc_type = type(e).__name__
    
    if isinstance(e, requests.exceptions.Timeout):
        return "timeout"
    elif isinstance(e, requests.exceptions.ConnectionError):
        # Check if it's a DNS error (common in ConnectionError)
        if "Name or service not known" in str(e) or "getaddrinfo failed" in str(e):
            return "dns"
        return "connection_error"
    elif isinstance(e, requests.exceptions.SSLError):
        return "ssl"
    elif isinstance(e, requests.exceptions.RequestException):
        return "request_error"
    else:
        return "unknown_error"


def check_url(url: str, session: requests.Session, timeout: float = 6.0) -> Tuple[bool, str]:
    """
    Check if a URL is reachable and returns a valid status code.
    
    Strategy:
      1. Try HEAD request first (efficient, doesn't download body)
      2. If HEAD fails with 405 (Method Not Allowed) or 403 (Forbidden), retry with GET
      3. GET uses stream=True and closes immediately (no body download)
    
    Accept as valid:
      - Any 2xx (success) or 3xx (redirect) status codes
    
    Consider invalid:
      - 4xx or 5xx status codes
      - Timeouts, DNS failures, SSL errors, connection errors
    
    Args:
        url: URL string to check
        session: requests.Session object for connection reuse
        timeout: HTTP request timeout in seconds (default 6.0)
    
    Returns:
        Tuple (ok: bool, detail: str)
        - ok: True if URL is reachable with valid status
        - detail: Human-readable string (e.g., "HTTP 200", "HTTP 404", "timeout", "ssl", etc.)
    """
    if not url:
        return False, "empty_url"
    
    # Basic scheme validation
    if not (url.startswith("http://") or url.startswith("https://")):
        return False, "invalid_scheme"
    
    # Try HEAD first
    try:
        response = session.head(url, allow_redirects=True, timeout=timeout)
        status = response.status_code
        
        # Check if status is valid (2xx or 3xx)
        if 200 <= status < 400:
            return True, f"HTTP {status}"
        
        # If 405 (Method Not Allowed) or 403 (sometimes servers block HEAD), try GET
        if status in (405, 403):
            logger.debug("HEAD returned %d for %s; trying GET fallback", status, url)
            try:
                response = session.get(url, allow_redirects=True, timeout=timeout, stream=True)
                response.close()  # Close immediately without downloading body
                
                status = response.status_code
                if 200 <= status < 400:
                    return True, f"HTTP {status}"
                else:
                    return False, f"HTTP {status}"
            except Exception as e:
                err_class = classify_request_exception(e)
                logger.debug("GET fallback failed for %s: %s", url, err_class, exc_info=True)
                return False, err_class
        
        # Other 4xx or 5xx
        return False, f"HTTP {status}"
    
    except requests.exceptions.Timeout:
        logger.debug("URL check timeout for %s", url)
        return False, "timeout"
    except requests.exceptions.SSLError:
        logger.debug("URL check SSL error for %s", url, exc_info=True)
        return False, "ssl"
    except requests.exceptions.ConnectionError as e:
        # Classify DNS vs connection error
        if "Name or service not known" in str(e) or "getaddrinfo failed" in str(e):
            logger.debug("URL check DNS error for %s", url, exc_info=True)
            return False, "dns"
        logger.debug("URL check connection error for %s", url, exc_info=True)
        return False, "connection_error"
    except requests.exceptions.RequestException as e:
        logger.debug("URL check request error for %s: %s", url, type(e).__name__, exc_info=True)
        return False, classify_request_exception(e)
    except Exception as e:
        logger.debug("URL check unexpected error for %s: %s", url, type(e).__name__, exc_info=True)
        return False, "unknown_error"
