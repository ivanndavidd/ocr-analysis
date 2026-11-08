ALTER TABLE "OCRContractDetail" ADD COLUMN "result" varchar;--> statement-breakpoint
ALTER TABLE "OCRContractDetail" ADD COLUMN "reusedFromHeaderId" integer;--> statement-breakpoint
ALTER TABLE "OCRHeader" ADD COLUMN "jobId" uuid DEFAULT gen_random_uuid() NOT NULL;--> statement-breakpoint
ALTER TABLE "OCRHeader" ADD COLUMN "mode" varchar(20) DEFAULT 'ocr' NOT NULL;--> statement-breakpoint
ALTER TABLE "OCRHeader" ADD COLUMN "claude" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "OCRContractDetail" ADD CONSTRAINT "OCRContractDetail_reusedFromHeaderId_OCRHeader_id_fk" FOREIGN KEY ("reusedFromHeaderId") REFERENCES "public"."OCRHeader"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "OCRHeader" ADD CONSTRAINT "OCRHeader_jobId_unique" UNIQUE("jobId");--> statement-breakpoint
-- runs saved before this column: Claude helped if their files used Claude tokens
UPDATE "OCRHeader" SET "claude" = true WHERE "inputTokens" + "cacheWriteTokens" + "cacheReadTokens" + "outputTokens" > 0;
