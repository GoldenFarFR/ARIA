# Runbook — VPS SSH troubleshooting (broken/lost/miscopied key)

> **PUBLIC repo — never a real IP/secret/access here.** This procedure is deliberately
> generic (no real IP/name) — extracted from `CLAUDE.md` on 08/03 (compaction pass).

If SSH access to a VPS from an operator machine breaks (compromised key, miscopied key,
lost access), follow this order — **never delete/revoke anything before confirming a
replacement access actually works** (same rule as any secret rotation):

1. **Generate a clean key**: `ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\<name>"`. The
   file name has no functional bearing for OpenSSH — prefer a name **without spaces** (see
   point 5).
2. **Find a fallback access path** to place the new public key on the VPS, in this order of
   preference: (a) an already-active Claude Code session on the VPS (direct shell access, no
   SSH needed); (b) another already-authorized device (pull its private key from the password
   manager, temporarily replay it on the blocked machine); (c) the hosting provider's web
   console (KVM/VNC — goes through the system login/PAM, **independent** of the SSH
   `PasswordAuthentication no` setting, unless the root password has also been locked at the
   system level, in which case only the hosting provider's support can help); (d) as a last
   resort, contact the hosting provider's support.
   **Never click a "reinstall image" option in a hosting panel** — it wipes the entire server.
3. **Add the new public key** to `~/.ssh/authorized_keys` on the VPS (append, always after a
   `cp` backup of the file, never a direct overwrite).
4. **Verify the new access works** (new terminal window) before removing anything from
   `authorized_keys`.
5. **Windows copy-paste pitfalls encountered in practice**:
   - A copy-paste from a password manager (free-text field/note, not a dedicated "SSH Key"
     type) can flatten a multi-line private key into a single line (line breaks replaced by
     spaces) → `ssh` returns `invalid format`. Fix: extract only the valid base64 characters
     and rebuild the 3 lines (`BEGIN`/body/`END`), write as ASCII without a BOM
     (`[System.IO.File]::WriteAllText(..., [System.Text.Encoding]::ASCII)`).
   - A key file name **with a space** breaks `~/.ssh/config` (requires quoting the
     `IdentityFile` path) and also breaks Claude Code's internal SSH client (next point) →
     prefer a name without spaces from the start.
   - Pasting a multi-line PowerShell block (here-string `@"..."@`) into a terminal can execute
     line by line instead of as a whole block and corrupt the generated file → prefer a series
     of `Set-Content`/`Add-Content` commands (one line = one complete command), more robust to
     pasting.
   - Always fix the key file's permissions on Windows before use:
     `icacls <file> /inheritance:r` then `icacls <file> /grant:r "<user>:(F)"` — use `(F)`, not
     `(R)`, otherwise the file can't be fixed afterward.
   - Claude Code's built-in SSH client (remote connections) is **not** native OpenSSH — it
     never reads `~/.ssh/config` and doesn't understand `~` on Windows in the "Identity File"
     field: enter the **full absolute path** there (`C:\Users\<user>\.ssh\<file>`), never
     `~/...`.
   - Claude Code can rewrite `~/.ssh/config` when saving its own connection configuration and
     drop an `IdentityFile` line added manually — recheck `config` after any save in Claude
     Code's SSH connection interface (a Windows SSH agent that has cached the key can
     temporarily mask the problem — `ssh` still works without `IdentityFile` as long as the
     agent is running, but this isn't reliable after a reboot: put the line back anyway).
6. **Password manager (e.g. Bitwarden-type)**: store an SSH key in the dedicated "SSH Key"
   item type (not a free-text note/custom field) to avoid point 5 — this type preserves the
   format correctly on export/copy. If the tool only supports **generating** a new key (no
   import), add that generated key to the VPS and migrate to it (same steps 2-4), rather than
   forcing an import that fails.
7. **Once the new access is confirmed and in real use**, remove the old key from
   `authorized_keys`, delete local key files that are no longer needed, and update/remove any
   stale entry in the password manager.

**Security reminder**: if a private key's contents were ever displayed in the clear anywhere
(screenshot, chat, log), treat it as compromised immediately — generate a new pair, never
reuse the old one beyond a temporary bridge to its replacement. **VPS IP and access details
stay private, in `aria-ops`** — this procedure is deliberately generic (no real IP/name).
