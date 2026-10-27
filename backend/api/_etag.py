# api/_etag.py
"""
ETag helper for stable-data list endpoints (B-020).

Usage
-----
  from api._etag import etag_response
  from fastapi import Request
  from fastapi.encoders import jsonable_encoder

  @router.get("/widgets")
  async def list_widgets(request: Request, auth=Depends(get_current_auth)):
      data = build_widget_list()
      return etag_response(request, jsonable_encoder(data))

How it works
------------
The caller passes already-serialisable data (plain dicts/lists — use
`jsonable_encoder` to convert Pydantic models first).  `etag_response`
computes an MD5 fingerprint of the JSON body, compares it against the
client's ``If-None-Match`` request header, and returns either:

  - 304 Not Modified  (empty body, same ETag header) when the content
    has not changed since the client's last fetch.
  - 200 JSONResponse  with ``ETag`` and ``Cache-Control: private, no-cache``
    headers when the content is new or no cached version exists.

``Cache-Control: private, no-cache`` means the client must revalidate
before reusing any cached copy, which is exactly the ETag contract.

MD5 is used intentionally — ETags require speed, not cryptographic
security.  The fingerprint is of the serialised response body, so any
change to any field produces a different ETag automatically.
"""

import hashlib
import json

from fastapi import Request
from fastapi.responses import JSONResponse, Response


def etag_response(request: Request, data) -> Response:
    """
    Return a 304 or 200 response depending on whether the client's cached
    ETag matches the current response body fingerprint.

    Parameters
    ----------
    request : fastapi.Request
        The incoming request (used to read ``If-None-Match``).
    data :
        JSON-serialisable value (list or dict).  Pass the output of
        ``fastapi.encoders.jsonable_encoder(your_pydantic_list)`` here.

    Returns
    -------
    fastapi.responses.Response
        Either a 304 with an empty body or a 200 JSONResponse.
    """
    body = json.dumps(
        data,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    # MD5 — fast, non-cryptographic, sufficient for HTTP ETags
    digest = hashlib.md5(body).hexdigest()  # noqa: S324
    etag = f'"{digest}"'

    if request.headers.get("if-none-match") == etag:
        # Content unchanged — tell the client to use its cache
        return Response(status_code=304, headers={"ETag": etag})

    return JSONResponse(
        content=data,
        headers={
            "ETag": etag,
            "Cache-Control": "private, no-cache",
        },
    )
