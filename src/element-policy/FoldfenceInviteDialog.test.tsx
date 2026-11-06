/*
Copyright 2024 New Vector Ltd.
Copyright 2022 The Matrix.org Foundation C.I.C.

SPDX-License-Identifier: AGPL-3.0-only OR GPL-3.0-only OR LicenseRef-Element-Commercial
Please see LICENSE files in the repository root for full details.
*/

// @vitest-environment happy-dom

import { vi, describe, it, expect, afterAll, beforeEach, afterEach, type Mocked } from "vitest";

import React from "react";
import { render, screen, waitFor } from "test-utils-rtl";
import userEvent from "@testing-library/user-event";
import { type MatrixClient, MatrixError, Room } from "matrix-js-sdk/src/matrix";
import { KnownMembership } from "matrix-js-sdk/src/types";
import { UserVerificationStatus } from "matrix-js-sdk/src/crypto-api";
import {
    clearAllModals,
    filterConsole,
    flushPromises,
    getMockClientWithEventEmitter,
    mkMembership,
    mkMessage,
    mkRoomCreateEvent,
    TestSDKContext,
} from "test-utils";

import InviteDialog from "./InviteDialog";
import { InviteKind } from "./InviteDialogTypes";
import DMRoomMap from "../../../utils/DMRoomMap";
import SdkConfig from "../../../SdkConfig";
import { type ValidatedServerConfig } from "../../../utils/ValidatedServerConfig";
import { type IConfigOptions } from "../../../IConfigOptions";
import { SDKContextClass } from "../../../contexts/SDKContextClass";
import { type IProfileInfo } from "../../../hooks/useProfileInfo";
import { startDmOnFirstMessage } from "../../../utils/direct-messages";
import { MatrixClientPeg } from "../../../MatrixClientPeg.ts";

const mockGetAccessToken = vi.fn().mockResolvedValue("getAccessToken");
vi.mock("../../../IdentityAuthClient", () => ({
    default: vi.fn().mockImplementation(function () {
        return {
            getAccessToken: mockGetAccessToken,
        };
    }),
}));

vi.mock("../../../utils/direct-messages", async () => ({
    ...(await vi.importActual("../../../utils/direct-messages")),
    startDmOnFirstMessage: vi.fn(),
}));

vi.mock("../../../dispatcher/dispatcher");

vi.mock("lodash", async () => ({
    ...(await vi.importActual("lodash")),
    // Stub out the debounce to prevent async leaks
    debounce: vi.fn((fn) => {
        fn.cancel = vi.fn();
        return fn;
    }),
}));

const getSearchField = () => screen.getByTestId("invite-dialog-input");

const pasteIntoSearchField = async (value: string) => {
    const searchField = getSearchField();
    await userEvent.clear(searchField);
    searchField.focus();
    await userEvent.paste(value);
};

const serverDomain = "example.org";
const roomId = "!111111111111111111:example.org";
const aliceId = "@alice:example.org";
const bobId = "@bob:example.org";

const aliceProfileInfo: IProfileInfo = {
    user_id: aliceId,
    display_name: "Alice",
};

const bobProfileInfo: IProfileInfo = {
    user_id: bobId,
    display_name: "Bob",
};

describe("Foldfence policy-aware InviteDialog", () => {
    let mockClient: Mocked<MatrixClient>;
    let room: Room;
    let sdkContext: TestSDKContext;

    filterConsole(
        "Error retrieving profile for userId @carol:example.com",
        "Error retrieving profile for userId @localpart:server.tld",
        "Error retrieving profile for userId @localpart:server:tld",
        "[Invite:Recents] Excluding @alice:example.org from recents",
    );

    beforeEach(async () => {
        mockClient = getMockClientWithEventEmitter({
            getCrypto: vi.fn().mockReturnValue({
                getUserVerificationStatus: vi
                    .fn()
                    .mockResolvedValue(new UserVerificationStatus(false, false, true, false)),
            }),
            getDomain: vi.fn().mockReturnValue(serverDomain),
            getUserId: vi.fn().mockReturnValue(bobId),
            getSafeUserId: vi.fn().mockReturnValue(bobId),
            isGuest: vi.fn().mockReturnValue(false),
            getVisibleRooms: vi.fn().mockReturnValue([]),
            getRoom: vi.fn(),
            getRooms: vi.fn(),
            getAccountData: vi.fn(),
            getPushActionsForEvent: vi.fn(),
            mxcUrlToHttp: vi.fn().mockReturnValue(""),
            isRoomEncrypted: vi.fn().mockReturnValue(false),
            getProfileInfo: vi.fn().mockImplementation(async (userId: string) => {
                if (userId === aliceId) return aliceProfileInfo;
                if (userId === bobId) return bobProfileInfo;

                throw new MatrixError({
                    errcode: "M_UNKNOWN",
                    error: "Profile not found",
                });
            }),
            getIdentityServerUrl: vi.fn(),
            searchUserDirectory: vi.fn().mockResolvedValue({}),
            lookupThreePid: vi.fn(),
            registerWithIdentityServer: vi.fn().mockResolvedValue({
                access_token: "access_token",
                token: "token",
            }),
            getOpenIdToken: vi.fn().mockResolvedValue({}),
            getIdentityAccount: vi.fn().mockResolvedValue({}),
            getTerms: vi.fn().mockResolvedValue({ policies: [] }),
            supportsThreads: vi.fn().mockReturnValue(false),
            isInitialSyncComplete: vi.fn().mockReturnValue(true),
            getClientWellKnown: vi.fn().mockResolvedValue({}),
            invite: vi.fn(),
        });
        SdkConfig.put({ validated_server_config: {} as ValidatedServerConfig } as IConfigOptions);
        DMRoomMap.makeShared(mockClient);
        vi.clearAllMocks();
        vi.spyOn(MatrixClientPeg, "safeGet").mockReturnValue(mockClient);

        room = new Room(roomId, mockClient, mockClient.getSafeUserId());
        await room.addLiveEvents(
            [
                mkMessage({
                    msg: "Hello",
                    relatesTo: undefined,
                    event: true,
                    room: roomId,
                    user: mockClient.getSafeUserId(),
                    ts: Date.now(),
                }),
            ],
            { addToState: true },
        );
        room.currentState.setStateEvents([
            mkRoomCreateEvent(bobId, roomId),
            mkMembership({
                event: true,
                room: roomId,
                mship: KnownMembership.Join,
                user: aliceId,
                skey: aliceId,
            }),
        ]);
        vi.spyOn(DMRoomMap.shared(), "getUniqueRoomsWithIndividuals").mockReturnValue({
            [aliceId]: room,
        });
        mockClient.getRooms.mockReturnValue([room]);
        mockClient.getRoom.mockReturnValue(room);

        sdkContext = new TestSDKContext();
        // @ts-ignore UserMenuViewModel uses SDKContext in the constructor
        SDKContextClass.instance = sdkContext;
        sdkContext._client = mockClient;
    });

    afterEach(async () => {
        await clearAllModals();
        SDKContextClass.instance.onLoggedOut();
    });

    afterAll(() => {
        vi.restoreAllMocks();
    });


    const allowedRecent = aliceId;
    const forbiddenRecent = "@carol:example.org";
    const allowedSuggestion = "@dave:example.org";
    const forbiddenSuggestion = "@eve:example.org";

    beforeEach(() => {
        room.currentState.setStateEvents([
            ...[forbiddenRecent, allowedSuggestion, forbiddenSuggestion].map((id) =>
                mkMembership({event: true, room: roomId, mship: KnownMembership.Join, user: id, skey: id})),
        ]);
        vi.spyOn(room, "getMyMembership").mockReturnValue(KnownMembership.Join);
        vi.spyOn(DMRoomMap.shared(), "getUserIdForRoomId").mockReturnValue(undefined);
        vi.mocked(DMRoomMap.shared().getUniqueRoomsWithIndividuals).mockReturnValue({
            [allowedRecent]: room, [forbiddenRecent]: room,
        });
        mockClient.getAccessToken = vi.fn().mockReturnValue("synthetic-fixture-session");
        mockClient.getHomeserverUrl = vi.fn().mockReturnValue("https://example.org/");
        vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify({
            schema_version: 1, user_id: bobId, expires_at: new Date(Date.now() + 30_000).toISOString(),
            allowed_user_ids: [allowedRecent, allowedSuggestion],
        }), {status: 200})));
    });

    afterEach(() => vi.unstubAllGlobals());

    it("filters cached recents and room-derived suggestions using the current contact policy", async () => {
        render(<InviteDialog kind={InviteKind.Dm} onFinished={vi.fn()} />);
        await waitFor(() => expect(screen.queryAllByText(allowedSuggestion).length).toBeGreaterThan(0));
        expect(screen.queryAllByText(allowedRecent).length).toBeGreaterThan(0);
        expect(screen.queryByText(forbiddenRecent)).not.toBeInTheDocument();
        expect(screen.queryByText(forbiddenSuggestion)).not.toBeInTheDocument();
    });

    it("does not resolve or display a typed forbidden MXID through the public-profile fallback", async () => {
        const getProfile = vi.spyOn(sdkContext.userProfilesStore, "getOrFetchProfile");
        render(<InviteDialog kind={InviteKind.Dm} onFinished={vi.fn()} />);
        await waitFor(() => expect(screen.queryAllByText(allowedSuggestion).length).toBeGreaterThan(0));
        await userEvent.type(getSearchField(), forbiddenSuggestion);
        await waitFor(() => expect(mockClient.searchUserDirectory).toHaveBeenCalledWith({term: forbiddenSuggestion}));
        await flushPromises();
        expect(getProfile).not.toHaveBeenCalledWith(forbiddenSuggestion, expect.anything());
        expect(screen.queryByText(forbiddenSuggestion)).not.toBeInTheDocument();
        expect(screen.getByRole("button", {name: "Go"})).toHaveAttribute("aria-disabled", "true");
    });

    it("replaces the selected DM contact instead of submitting a multi-person draft", async () => {
        render(<InviteDialog kind={InviteKind.Dm} onFinished={vi.fn()} />);
        await waitFor(() => expect(screen.queryAllByText(allowedSuggestion).length).toBeGreaterThan(0));
        await userEvent.click(screen.getByRole("option", {name: allowedRecent}));
        await userEvent.click(screen.getByRole("option", {name: allowedSuggestion}));
        await userEvent.click(screen.getByRole("button", {name: "Go"}));
        await waitFor(() => expect(startDmOnFirstMessage).toHaveBeenCalledOnce());
        const submitted = vi.mocked(startDmOnFirstMessage).mock.calls[0][1];
        expect(submitted.map((member) => member.userId)).toEqual([allowedSuggestion]);
    });

    it("pastes only the first allowed DM contact and ignores a forbidden leading ID", async () => {
        render(<InviteDialog kind={InviteKind.Dm} onFinished={vi.fn()} />);
        await waitFor(() => expect(screen.queryAllByText(allowedSuggestion).length).toBeGreaterThan(0));
        await pasteIntoSearchField(`${forbiddenRecent} ${allowedRecent} ${allowedSuggestion}`);
        await userEvent.click(screen.getByRole("button", {name: "Go"}));
        await waitFor(() => expect(startDmOnFirstMessage).toHaveBeenCalledOnce());
        const submitted = vi.mocked(startDmOnFirstMessage).mock.calls[0][1];
        expect(submitted.map((member) => member.userId)).toEqual([allowedRecent]);
    });
});
