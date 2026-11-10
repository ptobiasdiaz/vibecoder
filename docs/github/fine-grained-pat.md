# Fine-grained GitHub PAT

Create one Fine-grained Personal Access Token for the repositories that
vibecoder will manage. `vibecoder github login` uses `GET /user`, which requires
no fine-grained permission, to establish numeric identity. This proves neither
repository scope nor write access.

## Permissions required by the complete workflow

| Surface | Access | Used for |
|---|---|---|
| Metadata | Read | repository identity, default branch and repository metadata |
| Contents | Read and write | branches/commits and Git clone, fetch and push over HTTPS |
| Issues | Read and write | Issues, issue comments, events and labels |
| Pull requests | Read and write | PR creation/status, reviews and line comments |
| Workflows | Write, conditional | only if an authorised task modifies `.github/workflows/` |

No repository Administration permission is requested. Select every repository
that vibecoder must manage; organisation policy may require owner approval.

Endpoint families audited in the current adapter: `/user`, `/repos/{owner}/{repo}`,
commits, branches, Issues, issue comments/events/labels, pulls, pull reviews,
review comments and PR commits. Git operations use HTTPS separately from REST.

GitHub documents [`GET /user`](https://docs.github.com/en/rest/users/users),
[fine-grained endpoint permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens),
[Issues](https://docs.github.com/en/rest/issues/issues),
[pull requests](https://docs.github.com/en/rest/pulls/pulls), and
[review comments](https://docs.github.com/en/rest/pulls/comments).

`X-Accepted-GitHub-Permissions` describes permissions accepted by the endpoint;
it does not enumerate what the token was granted. Vibecoder performs no fake
write. A 404 may mean absent resource or hidden/out-of-scope repository, and is
reported as ambiguous. See GitHub's
[REST troubleshooting guidance](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api).

GitHub does not expose a reliable endpoint that proves every effective write
permission, nor does `/user` prove that the credential is fine-grained rather
than another accepted token type. Vibecoder therefore documents and requires a
Fine-grained PAT but does not infer write access from successful login.

