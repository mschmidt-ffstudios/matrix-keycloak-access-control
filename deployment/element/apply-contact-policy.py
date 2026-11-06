#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Apply the reviewed contact-picker changes to the exact Element source archive."""
from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_SHA256 = "f6d45cdcbe4af1d51ea5c6019d29d2c7371ddc06bc9b9095c623ca2fe4a45190"
INVITE_SHA256 = "8b0ce2c2e0712688375c651ee371912522463b35c815bcf44f14b13c580ba2a1"


def patch(source: Path) -> None:
    target = source / "apps/web/src/components/views/dialogs/InviteDialog.tsx"
    original = target.read_text(encoding="utf-8")
    if hashlib.sha256(target.read_bytes()).hexdigest() != INVITE_SHA256:
        raise SystemExit("Element InviteDialog does not match the reviewed v1.12.33 source")
    replacements = [
        ('import { MatrixClientPeg } from "../../../MatrixClientPeg";',
         'import { MatrixClientPeg } from "../../../MatrixClientPeg";\nimport { loadContactGrant, permitsContact, type ContactGrant } from "../../../FoldfenceContactPolicy";'),
        ('interface IInviteDialogState {', 'interface IInviteDialogState {\n    contactGrant: ContactGrant | null;'),
        ('    private unmounted = false;',
         '    private unmounted = false;\n    private contactPolicyTimer?: ReturnType<typeof setInterval>;\n    private contactPolicyLoading = false;'),
        ('        this.state = {\n            targets:', '        this.state = {\n            contactGrant: null,\n            targets:'),
        ('        this.unmounted = false;\n        this.encryptionByDefault',
         '        this.unmounted = false;\n        if (this.props.kind === InviteKind.Dm) {\n            void this.refreshContactPolicy();\n            this.contactPolicyTimer = setInterval(() => void this.refreshContactPolicy(), 5_000);\n        }\n        this.encryptionByDefault'),
        ('        this.unmounted = true;\n        this.updateSuggestions.cancel();',
         '        this.unmounted = true;\n        clearInterval(this.contactPolicyTimer);\n        this.updateSuggestions.cancel();'),
        ('    private onConsultFirstChange = ', '''    private isAllowedContact = (userId: string): boolean => {
        return this.props.kind !== InviteKind.Dm || permitsContact(this.state.contactGrant, userId);
    };

    private refreshContactPolicy = async (): Promise<void> => {
        if (this.contactPolicyLoading) return;
        this.contactPolicyLoading = true;
        let contactGrant: ContactGrant | null = null;
        try {
            contactGrant = await loadContactGrant(MatrixClientPeg.safeGet());
        } catch {
            // A missing or expired policy never falls back to local contacts.
        } finally {
            this.contactPolicyLoading = false;
        }
        if (!this.unmounted) {
            this.setState((state) => ({
                contactGrant,
                targets: state.targets.filter((target) => permitsContact(contactGrant, target.userId)),
            }));
        }
    };

    private onConsultFirstChange = '''),
        ('        if (!newMember) return this.state.targets;',
         '        if (!newMember || !this.isAllowedContact(newMember.userId)) return this.state.targets;'),
        ('        const newTargets = [...(this.state.targets || []), newMember];',
         '        const newTargets = this.props.kind === InviteKind.Dm ? [newMember] : [...(this.state.targets || []), newMember];'),
        ('            const targets = this.convertFilter();\n            await startDmOnFirstMessage(cli, targets);',
         '''            const targets = this.convertFilter();
            // Recheck immediately before creating even a local draft. Synapse
            // independently authorizes the eventual create/invite/send requests.
            const grant = await loadContactGrant(cli);
            if (targets.length !== 1 || !targets.every((target) => permitsContact(grant, target.userId))) {
                throw new Error("No authorized direct-chat contact selected");
            }
            await startDmOnFirstMessage(cli, targets);'''),
        ('                    if (term[0] === "@" && term.indexOf(":") > 1) {',
         '                    if (term[0] === "@" && term.indexOf(":") > 1 && this.isAllowedContact(term)) {'),
        ('    private toggleMember = (member: Member): void => {\n        if (!this.state.busy)',
         '    private toggleMember = (member: Member): void => {\n        if (!this.isAllowedContact(member.userId)) return;\n        if (!this.state.busy)'),
        ('                if (this.props.kind === InviteKind.CallTransfer && targets.length > 0) {',
         '                if ((this.props.kind === InviteKind.Dm || this.props.kind === InviteKind.CallTransfer) && targets.length > 0) {'),
        ('        for (const address of potentialAddresses) {',
         '        for (const address of potentialAddresses) {\n            if (!this.isAllowedContact(address)) continue;\n            if (this.props.kind === InviteKind.Dm && this.state.targets.length + toAdd.length >= 1) continue;'),
        ('        sourceMembers = [...priorityAdditionalMembers, ...sourceMembers, ...otherAdditionalMembers];',
         '        sourceMembers = [...priorityAdditionalMembers, ...sourceMembers, ...otherAdditionalMembers]\n            .filter((member) => this.isAllowedContact(member.userId));'),
        ('        return this.state.targets.length > 0 || (!!this.state.filterText && this.state.filterText.includes("@"));',
         '''        if (this.props.kind === InviteKind.Dm) {
            return (this.state.targets.length === 1 && this.isAllowedContact(this.state.targets[0].userId)) ||
                (this.state.targets.length === 0 && this.state.filterText.startsWith("@") && this.isAllowedContact(this.state.filterText));
        }
        return this.state.targets.length > 0 || (!!this.state.filterText && this.state.filterText.includes("@"));'''),
        ('                    <h3>{_t("invite|send_link_prompt")}</h3>',
         '                    <p>Foldfence contact policy · <a href="/foldfence-source.tar.gz">Source code (AGPL)</a></p>\n                    <h3>{_t("invite|send_link_prompt")}</h3>'),
    ]
    for before, after in replacements:
        if original.count(before) != 1:
            raise SystemExit("Element contact policy anchor changed; review required")
        original = original.replace(before, after, 1)
    target.write_text(original, encoding="utf-8")
    shutil.copyfile(ROOT / "src/element-policy/FoldfenceContactPolicy.ts", source / "apps/web/src/FoldfenceContactPolicy.ts")
    regression = ROOT / "src/element-policy/FoldfenceInviteDialog.test.tsx"
    if regression.is_file():
        shutil.copyfile(regression, target.with_name("FoldfenceInviteDialog.test.tsx"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    patch(args.source.resolve())
