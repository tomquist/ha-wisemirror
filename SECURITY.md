# Security

WiseMirror mirrors have no authentication: anyone on the same network can
control them over UDP 8001 and TCP 8408, Wi-Fi credentials are sent in clear
text during setup, and firmware updates are not signed. These are properties
of the device and cannot be fixed by this integration. Keep mirrors on a
trusted network or VLAN.

To report a problem in the integration itself, open a private security
advisory on GitHub rather than a public issue.
