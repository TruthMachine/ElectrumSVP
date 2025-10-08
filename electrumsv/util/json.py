import os
import json

import json as _json

dumps = _json.dumps
loads = _json.loads



def read_json_dict(filename):
    if not os.path.exists(filename):
        return {}
    with open(filename, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def write_json_dict(filename, data):
    tmp_filename = f"{filename}.tmp"
    with open(tmp_filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_filename, filename)

