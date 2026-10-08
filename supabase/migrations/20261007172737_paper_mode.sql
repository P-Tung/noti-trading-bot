-- Separate research paper collection from verified paper evaluation.
alter table public.experiments
  add column paper_mode text not null default 'RESEARCH_PAPER'
  check (paper_mode in ('RESEARCH_PAPER', 'VERIFIED_PAPER'));
