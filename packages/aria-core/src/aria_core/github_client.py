"""GitHub REST API client — sandbox-gated writes."""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def token_info(self) -> dict[str, object]:
        """Classic PAT scopes via X-OAuth-Scopes; fine-grained tokens often omit that header."""
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(f"{API}/user", headers=self._headers)
            r.raise_for_status()
            body = r.json()
            raw_scopes = r.headers.get("X-OAuth-Scopes", "")
            scopes = [s.strip() for s in raw_scopes.split(",") if s.strip()]
            return {
                "login": body.get("login"),
                "scopes": scopes,
                "fine_grained": len(scopes) == 0,
                "accepted_scopes": r.headers.get("X-Accepted-OAuth-Scopes", ""),
            }

    @staticmethod
    def delete_capable(scopes: list[str], *, fine_grained: bool) -> bool | None:
        if fine_grained:
            return None
        if not scopes:
            return False
        if "delete_repo" in scopes:
            return True
        return "repo" in scopes or "public_repo" in scopes

    async def repo_exists(self, owner: str, repo: str) -> bool:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(f"{API}/repos/{owner}/{repo}", headers=self._headers)
            return r.status_code == 200

    async def list_directory(self, owner: str, repo: str, path: str = "") -> list[dict[str, Any]]:
        url = f"{API}/repos/{owner}/{repo}/contents/{path}" if path else f"{API}/repos/{owner}/{repo}/contents"
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=self._headers)
            if r.status_code == 404:
                return []
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else [data]

    async def get_file_text(self, owner: str, repo: str, path: str) -> tuple[str, str | None]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{API}/repos/{owner}/{repo}/contents/{path}",
                headers=self._headers,
            )
            if r.status_code == 404:
                return "", None
            r.raise_for_status()
            body = r.json()
            raw = base64.b64decode(body["content"]).decode("utf-8")
            return raw, body.get("sha")

    async def list_org_repos(self, org: str) -> list[str]:
        """List repos for owner — includes private repos (token-scoped).

        `/users/{login}/repos` is public-only. GoldenFarFR is often a user account,
        not an org — authenticated `/user/repos` is required for private repos.
        """
        target = org.strip().lower()
        seen: set[str] = set()
        names: list[str] = []

        def _collect(batch: list[dict[str, Any]], *, filter_owner: bool) -> None:
            for item in batch:
                if filter_owner:
                    owner_login = (item.get("owner") or {}).get("login", "")
                    if owner_login.lower() != target:
                        continue
                name = item.get("name")
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Authenticated — private + public for accounts the token owns
            page = 1
            while True:
                r = await client.get(
                    f"{API}/user/repos",
                    headers=self._headers,
                    params={
                        "per_page": 100,
                        "page": page,
                        "affiliation": "owner",
                        "sort": "updated",
                    },
                )
                r.raise_for_status()
                batch = r.json()
                if not batch:
                    break
                _collect(batch, filter_owner=True)
                if len(batch) < 100:
                    break
                page += 1

            if names:
                return sorted(names)

            # Fallback — org or public user listing
            for url in (f"{API}/orgs/{org}/repos", f"{API}/users/{org}/repos"):
                page = 1
                while True:
                    r = await client.get(
                        url,
                        headers=self._headers,
                        params={"per_page": 100, "page": page, "type": "all"},
                    )
                    if r.status_code == 404:
                        break
                    r.raise_for_status()
                    batch = r.json()
                    if not batch:
                        break
                    _collect(batch, filter_owner=url.endswith(f"users/{org}/repos"))
                    if len(batch) < 100:
                        break
                    page += 1
                if names:
                    break

        return sorted(names)

    async def delete_repo(self, owner: str, repo: str) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.delete(
                f"{API}/repos/{owner}/{repo}",
                headers=self._headers,
            )
            if r.status_code == 404:
                raise FileNotFoundError(f"{owner}/{repo}")
            if r.status_code >= 400:
                detail = r.text[:300].strip() or r.reason_phrase
                raise RuntimeError(f"GitHub {r.status_code}: {detail}")
            if r.status_code not in (204, 200):
                r.raise_for_status()

    async def create_repo(
        self,
        owner: str,
        name: str,
        *,
        private: bool = True,
        description: str = "",
        auto_init: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "private": private,
            "description": description[:350],
            "auto_init": auto_init,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{API}/orgs/{owner}/repos",
                headers=self._headers,
                json=payload,
            )
            if r.status_code in (404, 403):
                r = await client.post(
                    f"{API}/user/repos",
                    headers=self._headers,
                    json=payload,
                )
            r.raise_for_status()
            return r.json()

    async def put_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str = "main",
        sha: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.put(
                f"{API}/repos/{owner}/{repo}/contents/{path}",
                headers=self._headers,
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    async def put_files_batch(
        self,
        owner: str,
        repo: str,
        files: list[tuple[str, str]],
        message: str,
        branch: str = "main",
    ) -> dict[str, Any]:
        """Create or update many files in a single commit (Git Trees API)."""
        if not files:
            raise ValueError("put_files_batch requires at least one file")
        ref_path = f"heads/{branch}"
        timeout = max(60.0, 30.0 + len(files) * 0.5)
        async with httpx.AsyncClient(timeout=timeout) as client:
            ref_r = await client.get(
                f"{API}/repos/{owner}/{repo}/git/ref/{ref_path}",
                headers=self._headers,
            )
            ref_r.raise_for_status()
            base_commit_sha = ref_r.json()["object"]["sha"]

            commit_r = await client.get(
                f"{API}/repos/{owner}/{repo}/git/commits/{base_commit_sha}",
                headers=self._headers,
            )
            commit_r.raise_for_status()
            base_tree_sha = commit_r.json()["tree"]["sha"]

            tree_items: list[dict[str, str]] = []
            for path, content in files:
                blob_r = await client.post(
                    f"{API}/repos/{owner}/{repo}/git/blobs",
                    headers=self._headers,
                    json={"content": content, "encoding": "utf-8"},
                )
                blob_r.raise_for_status()
                tree_items.append({
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_r.json()["sha"],
                })

            tree_r = await client.post(
                f"{API}/repos/{owner}/{repo}/git/trees",
                headers=self._headers,
                json={"base_tree": base_tree_sha, "tree": tree_items},
            )
            tree_r.raise_for_status()
            new_tree_sha = tree_r.json()["sha"]

            new_commit_r = await client.post(
                f"{API}/repos/{owner}/{repo}/git/commits",
                headers=self._headers,
                json={
                    "message": message,
                    "tree": new_tree_sha,
                    "parents": [base_commit_sha],
                },
            )
            new_commit_r.raise_for_status()
            new_commit_sha = new_commit_r.json()["sha"]

            update_r = await client.patch(
                f"{API}/repos/{owner}/{repo}/git/refs/{ref_path}",
                headers=self._headers,
                json={"sha": new_commit_sha, "force": False},
            )
            update_r.raise_for_status()
            return {
                "commit_sha": new_commit_sha,
                "files": len(files),
                "branch": branch,
            }

    async def get_branch_sha(self, owner: str, repo: str, branch: str = "main") -> str:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{API}/repos/{owner}/{repo}/git/ref/heads/{branch}",
                headers=self._headers,
            )
            r.raise_for_status()
            return r.json()["object"]["sha"]

    async def create_branch(
        self, owner: str, repo: str, branch: str, *, from_sha: str,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{API}/repos/{owner}/{repo}/git/refs",
                headers=self._headers,
                json={"ref": f"refs/heads/{branch}", "sha": from_sha},
            )
            if r.status_code == 422 and "Reference already exists" in r.text:
                return {"branch": branch, "exists": True}
            r.raise_for_status()
            return r.json()

    async def list_open_issues(
        self,
        owner: str,
        repo: str,
        *,
        per_page: int = 100,
        labels: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {"state": "open", "per_page": min(per_page, 100)}
        if labels:
            params["labels"] = labels
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{API}/repos/{owner}/{repo}/issues",
                headers=self._headers,
                params=params,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        *,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title[:256], "body": body[:65000]}
        if labels:
            payload["labels"] = labels[:5]
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{API}/repos/{owner}/{repo}/issues",
                headers=self._headers,
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        body: str,
        *,
        base: str = "main",
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{API}/repos/{owner}/{repo}/pulls",
                headers=self._headers,
                json={
                    "title": title[:256],
                    "head": head,
                    "base": base,
                    "body": body[:65000],
                },
            )
            r.raise_for_status()
            return r.json()

    async def list_issue_comments(
        self, owner: str, repo: str, issue_number: int
    ) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{API}/repos/{owner}/{repo}/issues/{issue_number}/comments",
                headers=self._headers,
                params={"per_page": 100},
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []

    async def list_pull_reviews(
        self, owner: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{API}/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
                headers=self._headers,
                params={"per_page": 100},
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []

    async def list_review_comments(
        self, owner: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]:
        """INLINE comments on a diff line (distinct from list_pull_reviews, which only
        returns a review's global body — often empty when a reviewer leaves only
        line-by-line suggestions, as on showcase.json)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{API}/repos/{owner}/{repo}/pulls/{pull_number}/comments",
                headers=self._headers,
                params={"per_page": 100},
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []

    async def create_issue_comment(
        self, owner: str, repo: str, issue_number: int, body: str
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{API}/repos/{owner}/{repo}/issues/{issue_number}/comments",
                headers=self._headers,
                json={"body": body[:65000]},
            )
            r.raise_for_status()
            return r.json()

    async def edit_issue_comment(
        self, owner: str, repo: str, comment_id: int, body: str
    ) -> dict[str, Any]:
        """Edits an existing issue/PR comment (PATCH). Reserved for the comment's author."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.patch(
                f"{API}/repos/{owner}/{repo}/issues/comments/{comment_id}",
                headers=self._headers,
                json={"body": body[:65000]},
            )
            r.raise_for_status()
            return r.json()

    async def count_merged_prs(
        self,
        owner: str,
        repo: str,
        *,
        author: str | None = None,
        days: int = 7,
    ) -> int:
        """Count merged PRs in last N days. Use search API (works for author too)."""
        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        q = f"repo:{owner}/{repo} is:pr is:merged merged:>={since}"
        if author:
            q += f" author:{author}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{API}/search/issues",
                headers=self._headers,
                params={"q": q, "per_page": 100},
            )
            if r.status_code != 200:
                return 0
            data = r.json()
            return int(data.get("total_count", 0))