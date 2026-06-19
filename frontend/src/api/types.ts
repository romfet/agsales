import type { components } from "./schema";

export type Client = components["schemas"]["ClientOut"];
export type ClientInfo = components["schemas"]["ClientInfoOut"];
export type ProductN4 = components["schemas"]["ProductN4Out"];
export type OrderSearch = components["schemas"]["OrderSearchOut"];
export type AnalyzeOut = components["schemas"]["AnalyzeOut"];
export type ForgottenItem = components["schemas"]["ForgottenItem"];
export type NicheItem = components["schemas"]["NicheItem"];

export type DraftLine = {
  n3: string;
  n4: string;
  item_guid: string;
  qty: number;
};
