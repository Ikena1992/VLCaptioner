"""Report Hub access failures without treating network failures as denials."""

from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError


class ModelAccessError(RuntimeError):
    pass


def load_with_access_error(loader, repo_id):
    try:
        return loader()
    except (HfHubHTTPError, LocalEntryNotFoundError) as error:
        cause = error
        seen = set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            status = getattr(getattr(cause, "response", None), "status_code", None)
            if isinstance(cause, GatedRepoError) or (
                isinstance(cause, HfHubHTTPError) and status in (401, 403)
            ):
                break
            cause = cause.__cause__
        else:
            raise
        raise ModelAccessError(
            f"Hugging Face denied access to {repo_id}. "
            f"Open https://huggingface.co/{repo_id} and accept/request access. "
            "Then authenticate on this computer with `hf auth login` using a "
            "read token from the same approved account (or set HF_TOKEN). "
            "Check that the token permits access to this gated model."
        ) from error
