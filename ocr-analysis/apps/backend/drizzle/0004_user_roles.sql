ALTER TABLE "User" ADD COLUMN "role" varchar(20) DEFAULT 'reviewer' NOT NULL;--> statement-breakpoint
ALTER TABLE "User" ADD COLUMN "active" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "User" ADD COLUMN "lastLoginAt" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "User" ADD CONSTRAINT "User_role_check" CHECK ("User"."role" in ('reviewer', 'admin'));--> statement-breakpoint
-- users already in the table take the defaults: inactive reviewers with no last login, until they sign in again and
-- an admin activates them; ADMIN_EMAILS makes the first admin
