import argparse
import hashlib
import logging
import os
import shutil
from pathlib import Path

from . import utils


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("infile", metavar="INPUT_FILE", default="rpms.in.yaml")
    parser.add_argument("--outfile", default="rpms.lock.yaml")

    args, rest = parser.parse_known_args()

    logging.basicConfig(level=logging.INFO)

    input_hash = hashlib.sha256()

    input_hash.update(b"\0".join(x.encode("utf-8") for x in rest))
    input_hash.update(b"\0")
    with open(args.infile, "rb") as f:
        while chunk := f.read(65536):
            input_hash.update(chunk)

    input_digest = input_hash.hexdigest()
    logging.info("Using %s as cache key", input_digest)

    cache_file = utils.CACHE_PATH / "results" / f"{input_digest}.yaml"
    cache_file.parent.mkdir(exist_ok=True, parents=True)

    cmd = os.environ.get("RPM_LOCKFILE_PROTOTYPE_CMD", "rpm-lockfile-prototype")

    if not cache_file.exists():
        logging.info("Cached results do not exist, running resolver")
        utils.logged_run(
            [cmd, args.infile, "--outfile", str(cache_file)] + rest,
            check=True,
        )

    logging.info("Copying cache results to %s", args.outfile)
    with cache_file.open("rb") as inp:
        with Path(args.outfile).open("wb") as outp:
            shutil.copyfileobj(inp, outp)
