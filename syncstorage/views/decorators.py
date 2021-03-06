# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from pyramid.httpexceptions import (HTTPNotFound,
                                    HTTPConflict,
                                    HTTPNotModified,
                                    HTTPPreconditionFailed)

from syncstorage.storage import (ConflictError,
                                 NotFoundError,
                                 InvalidOffsetError)

from syncstorage.views.util import (make_decorator,
                                    json_error,
                                    get_resource_version)

_ONE_MEG = 1024 * 1024

# How long the client should wait before retrying a conflicting write.
RETRY_AFTER = 5


@make_decorator
def convert_storage_errors(viewfunc, request):
    """View decorator to convert storage errors into HTTP error responses.

    This decorator intercepts any storage-backend exceptions and translates
    them into a matching HTTPError subclass.
    """
    try:
        return viewfunc(request)
    except ConflictError:
        headers = {"Retry-After": str(RETRY_AFTER)}
        raise HTTPConflict(headers=headers)
    except NotFoundError:
        raise HTTPNotFound
    except InvalidOffsetError:
        raise json_error(400, "error", [{
            "location": "querystring",
            "name": "offset",
            "description": "Invalid value for offset",
        }])


@make_decorator
def check_storage_quota(viewfunc, request):
    """View decorator to check the user's quota.

    This decorator checks if a write request (PUT or POST) would cause the
    user's storage quota to be exceeded.  If it would, then an appropriate
    error response is returned.

    In addition, if the user has less than one meg of quota remaining then
    it will include an "X-Quota-Remaining" header in the response.
    """
    # This only applies to write requests.
    if request.method not in ("PUT", "POST"):
        return viewfunc(request)

    storage = request.validated["storage"]
    userid = request.validated["userid"]
    quota_size = request.registry.settings.get("storage.quota_size")

    # Don't do anything if quotas are not enabled.
    if quota_size is None:
        return viewfunc(request)

    # Get the total size used from the underlying store, which may be cached.
    # If we're close to going over quota, ask it to recalculate fresher info.
    used = storage.get_total_size(userid)
    left = quota_size - used
    if left < _ONE_MEG:
        used = storage.get_total_size(userid, recalculate=True)
        left = quota_size - used

    # Look for new items that will be written by this request,
    # and subtract them from the remaining quota.
    new_bsos = request.validated.get("bsos")
    if new_bsos is None:
        new_bso = request.validated.get("bso")
        if new_bso is None:
            new_bsos = ()
        else:
            new_bsos = (new_bso,)

    for bso in new_bsos:
        left -= len(bso.get("payload", ""))

    # Report errors/warnings as appropriate.
    if left <= 0:  # no space left
        raise json_error(403, "quota-exceeded")
    if left < _ONE_MEG:
        request.response.headers["X-Quota-Remaining"] = str(left)

    return viewfunc(request)


@make_decorator
def check_precondition_headers(viewfunc, request):
    """View decorator to check X-If-[Unm|M]odified-Since-Version headers.

    This decorator checks pre-validated X-If-Modified-Since-Version and
    X-If-Unmodified-Since-Version headers against the actual last-modified
    version of the target resource.  If the preconditions are not met then
    it raises the appropriate error response.

    In addition, and retreived value for the last-modified version will be
    stored in the response headers for return to the client.  This may save
    having to look it up again when the response is being rendered.
    """
    if "if_modified_since" in request.validated:
        version = get_resource_version(request)
        request.response.headers["X-Last-Modified-Version"] = str(version)
        if version <= request.validated["if_modified_since"]:
            raise HTTPNotModified(headers={
                "X-Last-Modified-Version": str(version),
            })

    if "if_unmodified_since" in request.validated:
        version = get_resource_version(request)
        request.response.headers["X-Last-Modified-Version"] = str(version)
        if version > request.validated["if_unmodified_since"]:
            raise HTTPPreconditionFailed(headers={
                "X-Last-Modified-Version": str(version),
            })

    return viewfunc(request)


@make_decorator
def with_collection_lock(viewfunc, request):
    """View decorator to take a collection-level lock during request handling.

    This decorator will automatically take an appropriate collection-level lock
    and hold it while executing the view function.  Write requests will take
    a write lock, while read requests will take a read lock.

    If the request does not target a specific collection, no lock is taken.
    """
    storage = request.validated["storage"]
    userid = request.validated["userid"]
    collection = request.validated.get("collection")

    # If we're not operating on a collection, don't take a lock.
    if collection is None:
        return viewfunc(request)

    # Otherwise, take a read or write lock depending on request method.
    # To prevent silly bugs if additional methods are added, we explicitly
    # enumerating the safer read methods, and assume anything else is a write.
    if request.method in ("GET", "HEAD",):
        lock_collection = storage.lock_for_read
    else:
        lock_collection = storage.lock_for_write
    with lock_collection(userid, collection):
        return viewfunc(request)
