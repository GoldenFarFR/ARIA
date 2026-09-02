#!/bin/bash
export PATH=$PATH:/usr/local/bin:/usr/bin:/bin
export HOME=/root
cd /opt/aria

exec script -q -c "claude remote-control" /dev/null
