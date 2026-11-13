"""Shared, deterministic RDP security selection; no credentials are stored here."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shlex


@dataclass(frozen=True)
class SecurityDecision:
    mode: str
    reason: str
    options: list[str]
    fingerprint: str
    # The feed's aadtenantid, needed for FreeRDP's /azure:tenantid: override in
    # sovereign clouds. Empty when the .rdp does not carry one.
    aad_tenant_id: str = ''


def resolve_security(path, setting=None, extra='', remembered=None):
    properties = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        parts = line.split(':', 2)
        if len(parts) == 3:
            key, kind, value = (part.strip() for part in parts)
            properties[key.lower()] = (kind.lower(), value)
    relevant = {key: properties.get(key) for key in (
        'full address', 'aadtenantid', 'enablerdsaadauth', 'targetisaadjoined')}
    fingerprint = hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()
    tenant_property = properties.get('aadtenantid')
    tenant_id = tenant_property[1] if tenant_property else ''
    options, advanced = [], None
    for option in shlex.split(extra):
        if option.lower().startswith('/sec:'):
            advanced = option.partition(':')[2].lower()
            if advanced not in ('aad', 'nla'):
                raise ValueError('Advanced /sec: must be aad or nla')
        else:
            options.append(option)
    environment = os.environ.get('AVD_SEC', '').strip().lower()
    if environment not in ('', 'auto', 'aad', 'nla'):
        raise ValueError('AVD_SEC must be auto, aad or nla')
    if setting not in (None, '', 'auto', 'aad', 'nla'):
        raise ValueError('Connection authentication must be auto, aad or nla')
    for value, reason in ((environment, 'AVD_SEC override'),
                          (setting, 'connection setting'),
                          (advanced, 'advanced flags')):
        if value in ('aad', 'nla'):
            return SecurityDecision(value, reason, options, fingerprint, tenant_id)
    explicit = properties.get('enablerdsaadauth')
    if explicit is not None:
        mode = 'aad' if explicit == ('i', '1') else 'nla'
        return SecurityDecision(mode, 'feed enablerdsaadauth', options, fingerprint, tenant_id)
    if (isinstance(remembered, dict) and remembered.get('fingerprint') == fingerprint
            and remembered.get('mode') in ('aad', 'nla')):
        return SecurityDecision(remembered['mode'], 'previous successful connection', options,
                                fingerprint, tenant_id)
    return SecurityDecision('nla', 'no explicit Entra SSO flag; select Entra to test if supported',
                            options, fingerprint, tenant_id)
