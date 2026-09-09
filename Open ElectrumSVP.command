#!/bin/bash
cd "$(dirname "$0")"
source ./env/bin/activate
./electrum-sv
