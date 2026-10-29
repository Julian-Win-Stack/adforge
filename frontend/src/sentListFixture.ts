// Test support: the sent-list contract's fixture (backend tests/fixtures/sent_list.json), as
// the chat sends it on a clip: every picture and piece with a servable link beside its file.
import fixture from "../../backend/tests/fixtures/sent_list.json";
import type { SentList } from "./api";

const served = <T extends { file: string }>(one: T) => ({ ...one, url: `/media/${one.file}` });

/** The fixture as a clip's attachment carries it. */
export function sentListFixture(): SentList {
  return {
    ...fixture,
    pictures: fixture.pictures.map(served),
    pieces: fixture.pieces.map(served),
    left_out: fixture.left_out.map(served),
  } as SentList;
}

/** What a clip made before anything was recorded carries. */
export const EMPTY_SENT_LIST: SentList = {
  colour: "",
  pictures: [],
  pieces: [],
  left_out: [],
  recolours: [],
  prompt: "",
};
