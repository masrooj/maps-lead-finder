/** Panel owner: `leads` (Discover).
 * Discover = fresh pool only: not To contact, not Contacted, not Has booking.
 * Unmarking from To contact returns a lead here when those flags stay clear.
 * Loader still lives in app/runtime.js — peel into this file when touching load/paint.
 */
export const panelId = 'leads';

/** API status for the Discover city list. */
export const DISCOVER_STATUS = 'fresh';

/** True when a lead belongs on Discover (fresh discovered list). */
export function isFreshDiscoverLead(b){
  if(!b) return false;
  if(b.archived) return false;
  if(b.marked_for_contact) return false;
  if(b.contacted) return false;
  if(b.lost) return false;
  if(b.has_booking) return false;
  if((b.current_booking_software||'').trim()) return false;
  return true;
}

/** City Discover default when status is empty; keep explicit choices (incl. all). */
export function resolveDiscoverStatus(status){
  const s=String(status||'').trim();
  if(!s) return DISCOVER_STATUS;
  return s;
}
